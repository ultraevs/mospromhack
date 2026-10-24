# API

Описано то, что реализовано на текущем этапе. Схемы лежат в `shared/schemas.py`. Интерактивная документация: `http://localhost:8000/docs`.

Рамки везде нормированы: `[x, y, w, h]`, левый верхний угол и размер в долях кадра. Время в UTC, ISO 8601 с `Z`.

## Доступ

Сессия в httponly-cookie `linq_session`. Без неё всё под `/api`, `/media` и `/ws` отвечает 401 (WebSocket закрывается с кодом 4401).

- `POST /api/auth/login` `{login, password}` — ставит cookie, возвращает пользователя.
- `GET /api/auth/me` — текущий пользователь.
- `POST /api/auth/logout` — сессия удаляется.
- `GET/POST /api/auth/users`, `PATCH /api/auth/users/{id}` — только admin. Себя отключить или разжаловать нельзя, последний активный admin остаётся.

Роли: `admin` — всё; `viewer` (оператор смены) — камеры, журнал событий и разметка (`PATCH /api/events/{id}`), `/media`, `/ws/*`. У viewer логин и пароль в RTSP-адресе камеры заменены на `***`. Аналитика, настройки, уведомления и пользователи — только admin.

Служебные скрипты (bridge, demo, bench) ходят с заголовком `X-Internal-Token` и получают права admin.

## Публичный REST, префикс `/api`

### Камеры

| метод и путь | назначение |
|---|---|
| `GET /cameras` | список камер |
| `POST /cameras` | создать камеру, ответ 201 |
| `GET /cameras/{id}` | одна камера |
| `PATCH /cameras/{id}` | изменить любые поля |
| `DELETE /cameras/{id}` | удалить вместе с зонами, ответ 204 |
| `GET /videos` | видеофайлы в `data/videos`: `[{"name": "factory_floor.mp4", "size": 6927321}]` |

Тело создания:

```json
{"name": "Цех 2, ворота", "source_type": "file", "source": "factory_floor.mp4", "required_ppe": ["helmet"], "enabled": true}
```

`source_type`: `file` (имя файла из `data/videos`) или `rtsp` (адрес `rtsp://...`). `required_ppe`: подмножество `helmet, mask, vest`. Неверный источник: 422.

Ответ дополнен полями `id`, `status` (`online`, если воркер получает кадры, иначе `offline`) и `open_events`.

### Зоны

| метод и путь | назначение |
|---|---|
| `GET /cameras/{id}/zones` | зоны камеры |
| `POST /cameras/{id}/zones` | создать зону, ответ 201 |
| `PATCH /zones/{id}` | изменить любые поля |
| `DELETE /zones/{id}` | удалить, ответ 204 |

```json
{"name": "Зона резки", "type": "ppe_required", "polygon": [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]], "required_ppe": ["helmet", "mask"]}
```

`type`: `restricted` (вход запрещён), `ppe_required` (обязательны СИЗ из `required_ppe`), `flammable` (горючие материалы). `polygon`: не меньше трёх точек `[x, y]` в долях кадра 0..1, иначе 422. Изменение зоны действует без перезапуска: воркер подхватывает его опросом за 2 секунды.

### Параметры правил

| метод и путь | назначение |
|---|---|
| `GET /settings/rules` | текущие параметры |
| `PUT /settings/rules` | заменить все параметры |

```json
{
  "no_helmet": {"window_s": 4, "min_observations": 8, "fraction": 0.35},
  "no_mask": {"window_s": 4, "min_observations": 8, "fraction": 0.35},
  "no_vest": {"window_s": 6, "min_observations": 15, "max_fraction": 0.1, "min_person_height_px": 120},
  "zone_intrusion": {"window_s": 1.5, "fraction": 0.7, "min_observations": 4},
  "smoking": {"k": 3, "m": 5, "upper_fraction": 0.33, "everywhere": false},
  "fire": {"k": 3, "m": 5}, "smoke": {"k": 3, "m": 5},
  "lifecycle": {"close_after_s": 5, "cooldown_s": 60, "retrack_iou": 0.3, "retrack_window_s": 10,
                "camera_cooldown_s": 60, "track_lost_s": 2}
}
```

При первом запуске значения берутся из `config.yaml`, дальше хранятся в БД. Значения вне допустимых границ: 422.

### События

| метод и путь | назначение |
|---|---|
| `GET /events` | журнал, новые сверху |
| `GET /events/export?format=xlsx\|csv` | журнал файлом для отчёта, см. ниже |
| `GET /events/{id}` | одно событие |
| `PATCH /events/{id}` | разбор: `{"review": "confirmed"}`, `false_positive`, `forwarded` (передано мастеру) или `none` |

Параметры `GET /events`: `camera_id`, `type`, `severity`, `status`, `review`, `from`, `to` (по `started_at`), `limit` (1–500, по умолчанию 50), `offset`. Ответ:

```json
{"items": [ { "...событие..." } ], "total": 3, "limit": 50, "offset": 0}
```

Событие:

```json
{
  "id": "evt_NC2XF7YJPW", "type": "no_helmet", "severity": "medium", "status": "open",
  "camera_id": 2, "camera_name": "Цех (запись)", "zone_id": null, "zone_name": null, "track_id": null,
  "started_at": "2026-10-03T21:38:47.749000Z", "ended_at": null, "confidence": 0.622,
  "bbox": [0.8013, 0.6774, 0.0253, 0.0287],
  "snapshot_url": null, "snapshot_clean_url": null, "clip_url": null,
  "review": "none", "details": {"fraction": 0.38, "window_s": 4.0, "observations": 24}
}
```

**Выгрузка журнала.** `GET /events/export` отдаёт журнал файлом для отчёта по охране труда. По умолчанию это `xlsx`, есть `format=csv`.
- Доступ как у `GET /events`, viewer тоже может выгружать.
- Фильтры те же: `camera_id`, `zone_id`, `type`, `severity`, `status`, `review`, `from`, `to`. До 50 000 строк, новые сверху.
- Колонки: ID, начало и окончание (время сервера), длительность, камера, зона, нарушение, важность, состояние, разбор, уверенность, стоп-кадр (`/media/...`). Подписи по-русски.
- CSV в UTF-8 с BOM и разделителем `;`, так Excel сразу открывает кириллицу. Имя файла `events_ГГГГ-ММ-ДД.xlsx`.
- В журнале интерфейса это кнопка «Выгрузить в Excel», она учитывает текущие фильтры.

### Вебхуки

| метод и путь | назначение |
|---|---|
| `GET /webhooks` | список |
| `POST /webhooks` | создать, ответ 201 |
| `PATCH /webhooks/{id}` | изменить любые поля |
| `DELETE /webhooks/{id}` | удалить вместе с журналом, ответ 204 |
| `POST /webhooks/{id}/test` | отправить образец события одной попыткой, в ответе запись журнала |
| `GET /webhooks/{id}/deliveries?limit=50` | журнал доставок, новые сверху, по строке на попытку |

```json
{"url": "http://127.0.0.1:9100/hook", "secret": "demo-secret", "event_types": ["fire", "smoke"],
 "min_severity": "medium", "send_closed": false, "enabled": true}
```

`event_types`: пусто — все типы. `min_severity`: `low`, `medium` или `high`. `send_closed`: отправлять ли закрытие события.

Что получает адрес: `POST` с JSON события в теле и заголовками

- `X-Event-Type`: тип события, например `no_helmet`;
- `X-Event-Action`: `opened`, `clip_added`, `closed` или `test`;
- `X-Signature`: `sha256=<hex HMAC-SHA256 тела с секретом вебхука>`.

Проверка подписи на стороне получателя:

```python
expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
valid = hmac.compare_digest(expected, request.headers["X-Signature"])
```

Таймаут 5 секунд, до 3 повторов с паузами 1, 4 и 10 секунд. Запись журнала:

```json
{"id": 12, "webhook_id": 1, "event_id": "evt_NC30YYJB89", "action": "opened", "attempt": 1,
 "status_code": 200, "ok": true, "error": null, "duration_ms": 8, "created_at": "2026-10-03T22:38:22.105000Z"}
```

### Телеграм

`GET /telegram` — статус бота:

```json
{"enabled": true, "running": true, "username": "my_bot", "error": null, "subscribers": 2, "sent": 14, "errors": 0, "pending": 3}
```

`pending` — сколько событий ждёт сводки. Команды бота: `/start` подписывает чат, `/stop` отписывает, `/status` показывает число камер и открытых нарушений. Без `TELEGRAM_BOT_TOKEN` в `.env` бот выключен (`enabled: false`), остальное работает.

`PUT /telegram` с телом `{"enabled": true|false}` запускает или останавливает polling без перезапуска backend и сохраняет выбор в базе. Для включения требуется `TELEGRAM_BOT_TOKEN` в окружении.

`GET /settings/telegram`, `PUT /settings/telegram` — что и как часто уходит в чат. Действует со следующего события.

```json
{"instant_min_severity": "high", "min_interval_s": 1, "summary_interval_s": 60, "repeat_interval_s": 300}
```

- `instant_min_severity`: события этой важности и выше уходят сразу (фото со стоп-кадром, клип ответом), остальные — сводкой;
- `summary_interval_s`: период сводки;
- `repeat_interval_s`: тот же тип на той же камере упоминается не чаще раза за этот интервал, пропущенные приходят счётчиком;
- `min_interval_s`: пауза между сообщениями в один чат.

На журнал и вебхуки эти настройки не влияют.

### Аналитика

`GET /analytics/summary?from&to&days&camera_id&tz_offset_min` — счётчики за период. Все параметры необязательны; без `from`, `to` и `days` — за всё время. `days=7` или `days=30` считает период по часам backend, поэтому фильтр не зависит от рассинхрона часов браузера. `tz_offset_min` — смещение местного времени от UTC в минутах (для Москвы 180), нужно для разбивки по часам и дням.

```json
{
  "from": "2026-10-03T12:00:00Z", "to": null, "camera_id": null,
  "total": 5, "confirmed": 1, "false_positive": 2, "unreviewed": 2, "false_share": 0.667,
  "by_type": [{"type": "no_helmet", "total": 3, "confirmed": 1, "false_positive": 2, "unreviewed": 0, "false_share": 0.667}],
  "by_severity": [{"severity": "high", "total": 2}, {"severity": "medium", "total": 3}, {"severity": "low", "total": 0}],
  "by_camera": [{"camera_id": 1, "camera_name": "Цех 2, ворота", "total": 4}],
  "by_zone": [{"zone_id": 3, "zone_name": "Резка", "camera_name": "Цех 2, ворота", "total": 1}],
  "without_zone": 4,
  "by_hour": [{"hour": 0, "total": 0}],
  "by_day": [{"day": "2026-10-04", "total": 3}]
}
```

`false_share` — ложные / (ложные + подтверждённые), `null`, если размеченных нет. `by_type` содержит все семь типов, `by_hour` — все 24 часа. `by_camera` и `by_zone` отсортированы по убыванию.

### Файлы доказательств

`GET /media/events/<id>/snapshot.jpg` (кадр с рамкой нарушителя и зоной), `clean.jpg` (чистый кадр), `clip.mp4` (H.264, по умолчанию 5 секунд до и 5 после). Адреса приходят в полях события `snapshot_url`, `snapshot_clean_url`, `clip_url`; клип появляется через `post_seconds` после открытия.

### Состояние

| метод и путь | назначение |
|---|---|
| `GET /health` | состояние процессов; `status` равен `ok` или `degraded`, причины в `problems` |
| `GET /stats` | то, что прислал воркер: детектор, устройство, fps по камерам, время инференса |
| `GET /client-config` | `{"webrtc_port": 8889, "overlay_delay_ms": 0}` для браузера |

Пример `GET /stats`:

```json
{"worker_alive": true, "detector": "onnx", "device": "cuda", "warnings": [], "streams": 4,
 "target_fps": {"ppe": 6.0, "hazard": 1.0}, "actual_fps": {"ppe": 5.6, "hazard": 1.0},
 "inference": {"ppe": {"avg_ms": 24.0, "p95_ms": 36.5, "batch": 4}, "hazard": {"avg_ms": 54.2, "p95_ms": 117.0, "batch": 7}},
 "crops_per_s": 8.4, "crop_pass": {"cigarette": true, "head": false}, "loop_load": 0.38,
 "cameras": {"1": {"online": true, "error": null, "read_fps": 10.2, "proc_fps": 6.2}},
 "latency": {"capture_to_event": {"avg_ms": 94, "p95_ms": 188, "events": 100}},
 "subscribers": {"detections": 0, "events": 0}}
```

`inference.*` — время одного вызова детектора на весь батч; `actual_fps.ppe` — средняя частота обработки по камерам на связи; `loop_load` — доля времени, когда главный цикл воркера занят; `latency.capture_to_event` — от захвата кадра до записи события, по последним 100 событиям.

Пример `GET /health`:

```json
{"status": "ok", "problems": [], "backend": {"ok": true}, "mediamtx": {"ok": true, "error": null},
 "worker": {"ok": true, "detector": "onnx", "device": "cuda"},
 "sources": {"1": {"kind": "file", "publishing": true, "ffmpeg_running": true, "error": null}}}
```

## Интеграции

Сервис не ходит в СКУД, ERP и MES сам: готовых коннекторов нет. Для подключения есть четыре точки, все описаны выше.

- **Журнал событий по REST.** `GET /api/events` с фильтрами по камере, типу, важности, статусу, разбору и периоду, с постраничным выводом. Подходит, чтобы забирать события пакетами.
- **Вебхуки.** Событие уходит `POST`-ом с JSON и подписью `X-Signature` (HMAC-SHA256), с повторами и журналом доставок. Можно фильтровать по типу и важности, получать открытие, клип и закрытие (`X-Event-Action`).
- **Telegram.** Уведомления с фото и клипом для смены, настройки порога и частоты в `/api/settings/telegram`.
- **Служебный токен.** Скрипты и внешние сервисы могут ходить в публичный REST с заголовком `X-Internal-Token` (значение `INTERNAL_TOKEN` из `.env`), без cookie-сессии; права как у admin. Токен держать только на стороне сервера, наружу не отдавать.

**Разбор события** (`PATCH /api/events/{id}`, поле `review`): `confirmed`, `false_positive` (ложное срабатывание), `forwarded` (передано мастеру), `none`. Это отметка оператора, она видна в журнале, в аналитике (доля ложных по типам, `false_share`) и приходит в `/ws/events` как `event_updated`. Размеченные события вместе с кадрами (`clean.jpg`) задел под дообучение: ложные срабатывания можно выгрузить через `GET /api/events?review=false_positive` и использовать как отрицательные примеры. Автоматического дообучения по этим меткам сейчас нет, это план.

**Как подключить СКУД, ERP или MES.** Типичная схема: вебхук сервиса смотрит на HTTP-вход шины предприятия (или небольшого шлюза), шина проверяет подпись `X-Signature`, по `camera_id`, `zone_id` и `type` сопоставляет событие со своими объектами и передаёт дальше. Например, в СКУД это может быть блокировка пропуска сотрудника, в ERP или MES запись о нарушении в карточке участка или смены. Сопоставление камер и зон с объектами предприятия и сами адаптеры пишутся на стороне заказчика или как отдельный шлюз, в сервисе их нет. Для сверки в обратную сторону (например, поднимать тревогу по данным СКУД) входящего API нет, это план.

## WebSocket для браузера

- `/ws/detections?camera_id=N` — рамки одной камеры, сообщение на каждый обработанный кадр:

```json
{"type": "detections", "camera_id": 1, "ts": 1759581065120, "frame": {"w": 1920, "h": 1080},
 "people": [{"track_id": 17, "bbox": [0.41, 0.22, 0.08, 0.31], "conf": 0.83,
             "ppe": {"helmet": "violation"}, "zones": [], "violations": ["no_helmet"]}],
 "objects": [{"cls": "no_helmet", "bbox": [0.43, 0.22, 0.03, 0.05], "conf": 0.47, "track_id": null}]}
```

- `/ws/events` — `{"type": "event_created" | "event_updated", "event": {...}}`.

Видео браузер берёт напрямую из MediaMTX по WHEP: `POST http://<хост>:8889/cam-N/whep` с SDP-предложением.

## Внутренний API для воркера

Все запросы с заголовком `X-Internal-Token` (значение `INTERNAL_TOKEN` из `.env`), иначе 401.

| метод и путь | назначение |
|---|---|
| `GET /internal/config` | номер версии, включённые камеры с адресом `rtsp://127.0.0.1:8554/cam-N`, зонами и требованиями, параметры правил |
| `POST /internal/events` | открыть событие, ответ 201 с полным событием. Тело: JSON либо multipart с полем `event` (тот же JSON) и файлами `snapshot`, `clean` (JPEG) |
| `POST /internal/events/{id}/clip` | догрузить клип: multipart с файлом `clip` (mp4) |
| `PATCH /internal/events/{id}` | закрыть: `{"status": "closed", "ended_at": "..."}` |
| `POST /internal/stats` | статистика и пульс воркера, раз в секунду |
| `WS /internal/detections` | поток сообщений `detections`, backend пересылает их подписчикам `/ws/detections` |
