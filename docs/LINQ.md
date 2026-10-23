# Инференс на LinQ TPU

Как модели сервиса попадают на ускоритель LinQ, как проверяется точность после квантования и как замеряется скорость. SDK — `tpu_framework 2026.9.45` (компиляция). Инференс — облачная очередь TPU на стенде организаторов (RabbitMQ + `tpu_pycloud_format`), локальный `pytpu` тоже поддержан.

## Маршрут

```
models/<m>/best.onnx (YOLOv8s, [1,3,640,640])
  -> срез головы: на TPU всё до декодированных xy, wh и Sigmoid классов      scripts/linq_compile.py
  -> preliminary_model_conditioning -> calibrate_torch на реальных кадрах -> quantize (int8)
  -> compile_ (TPU_H1M, пресет O1) -> models/<m>/linq/<m>_b1_o1_h1m.tpu + meta.yaml

кадр -> letterbox/preprocess (как для onnx) -> TPU (облако или pytpu) -> хвост в numpy -> decode + NMS -> трекинг, правила
                                                 worker/app/detector/linq.py
```

Сервис вокруг детектора не меняется: `LinqDetector` возвращает тот же `[N, 4+nc, 8400]` float32, что и `OnnxDetector`. Включается `worker.detector: linq` в `config.yaml`.

### Срез головы

В документации SDK модели YOLO с detect-головой (`yolo12m`) обрезаны по `/model.21/Div_1`, `/model.21/Sub_1` и `/model.21/Sigmoid`. Остаток уходит в постобработку. У наших YOLOv8s голова того же вида, `linq_compile.py --cut auto` находит её сам:

```
output0 = Concat( Mul( Concat(/model.22/Div_1 (xy), /model.22/Sub_1 (wh)), strides ), /model.22/Sigmoid (классы) )
```

DFL, softmax и декод рамок считаются на TPU. На процессоре остаются два `Concat` и умножение на страйды 8/16/32 (`yolo_tail` в `worker/app/detector/linq.py`). Страйды берутся из модели в `strides.npy`, так что onnxruntime на стенде не нужен. Совпадение numpy-хвоста с отрезанным `tail.onnx` проверено, расхождение 0.

Тензоры программы (вывод `linq_compile.py`):

| тензор | форма | внутри TPU |
|---|---|---|
| `images` (вход) | (1, 3, 640, 640) float32 | дополняется до 4 каналов |
| `/model.22/Div_1_output_0` | (1, 2, 8400) | 8448 |
| `/model.22/Sub_1_output_0` | (1, 2, 8400) | 8448 |
| `/model.22/Sigmoid_output_0` | (1, nc, 8400) | 8448, масштаб на каждый якорь |

Упаковка и распаковка (масштаб, int8, паддинг) делаются рантаймом по метаданным программы. Вход и выход — обычные float32.

## Окружение SDK

SDK есть только под Linux x86_64 и Python 3.12.11 (tensorflow 2.20, torch 2.9.1, numpy 1.26.4), поэтому он собран в Docker. Колёса SDK в репозиторий не входят: их выдают организаторы, они лежат в `hack_materials/sdk/tpu_framework/packets`.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\linq_sdk.ps1 -Build          # образ linq-sdk, ~2 ГБ, torch без CUDA
powershell -ExecutionPolicy Bypass -File scripts\linq_sdk.ps1                 # bash в контейнере: /repo — репозиторий, /merged — MERGED_DIR из .env
```

Проверка SDK: эталонный `compile.py` из поставки собирает `torch_resnet50_b1_o1_h1m.tpu` за 57 с.

В версии 2026.9.45 пресет `O5_FGS` из документации не экспортируется. Доступны `O1`, `O3`, `O5`.

## Шаги (внутри контейнера)

```bash
# 1. калибровочный набор: 128 картинок обучающей выборки поровну по источникам + 64 кадра из роликов
python scripts/linq_calib.py --model ppe --images /merged/ppe/images/train --videos data/videos
# 2. срез, квантование, компиляция (около 1,5 минуты на модель)
python scripts/linq_compile.py --model ppe --preset o1 --calib data/linq/calib_ppe.npy --n-calib 64
# 3. точность int8 без TPU: квантованная голова через as_torch_graph + хвост, против float best.onnx на val
python scripts/linq_eval.py --model ppe --merged /merged --limit 300
# 4. эталон для сверки на стенде и архив для загрузки в Jupyter
python scripts/linq_eval.py --model ppe --merged /merged --ref 16
python scripts/linq_pack.py --models ppe hazard     # -> data/linq/linq_stand.zip
```

`linq_compile.py` дополнительно принимает `--samplewise 99.9` и `--act-percentile` (перцентили калибровки), `--finetune N` (дообучение порогов `fine_tune_torch`), `--cut none|auto|имена`, `--preset o1|o3|o5`.

Артефакты лежат в `models/<m>/linq/` и в git не попадают, кроме `meta.yaml`. `meta.yaml` описывает программу: вход, выходы TPU, хвост, параметры калибровки, время этапов.

## Точность после квантования

Квантованная модель проверяется на процессоре без TPU (`QuantizedModel.as_torch_graph` + `rescale`, как в e2e-примере SDK). На val сравниваются AP50 int8 и float32 по `scripts/eval_map.py`.

ppe, 300 картинок val, калибровка 64 кадра, O1:

| класс | AP50 float32 | AP50 int8 | разница |
|---|---|---|---|
| person | 0.8726 | 0.8657 | -0.0069 |
| helmet | 0.8116 | 0.8151 | +0.0035 |
| no_helmet | 0.8445 | 0.8585 | +0.0140 |
| vest | 0.4467 | 0.4573 | +0.0105 |
| mask | 0.5382 | 0.6127 | +0.0745 |
| no_mask | 0.8577 | 0.8683 | +0.0106 |
| vehicle | 0.7661 | 0.7925 | +0.0265 |
| **mAP50** | **0.7339** | **0.7529** | +0.0190 |

hazard скомпилирован тем же путём, его mAP ещё не посчитан.

**people** — COCO yolov8s для людей и техники. `scripts/make_people_model.py` урезает голову до 5 классов (person, car, motorcycle, bus, truck): в последних conv `cv3` оставлены только эти выходные каналы. На float это ничего не меняет, рамки и скоры совпадают с полной моделью с точностью 0.0. Зато выход классов на TPU 5×8448 вместо 80×8448.

Компилируется локально в `linq-sdk` за ~3.5 мин (квантование 96 с, компиляция 102 с):

```bash
python scripts/linq_calib.py --model people --images /merged/ppe/images/train --videos data/videos
python scripts/linq_compile.py --model people --preset o1 --calib data/linq/calib_people.npy --n-calib 64
python scripts/linq_eval.py --model people --merged /m --ref 48    # /m/people смонтирован на merged/ppe
```

Своей разметки под классы COCO нет, поэтому сверка идёт int8 против float на 48 кадрах val ppe. Совпадение детекций 0.944 (у ppe 0.696). Людей float находит 86, int8 81: int8 теряет на границе порога 0.5.

На стенде (raw): совпадение TPU с симуляцией 1.0, разница скоров 0.0. Пачка 1 — 70 мс/кадр, пачка 8 — 26 мс/кадр (38 кадров/с), столько же, сколько у ppe.

## Стенд

TPU стоит у организаторов, доступ через VPN и JupyterHub. `pytpu` и `/dev/tpu` в Jupyter нет: ускоритель отдаётся облачной очередью.

- **Брокер.** RabbitMQ на `10.0.114.30:5672`, exchange `tpu.direct`, ключ `input`.
- **Запрос.** Тело — `PyTask` из `tpu_pycloud_format`. В заголовке `uri` лежит путь к программе: `file:///auto/cache/<логин>/<prog>.tpu?raw=False`. Ответ приходит в очередь из `response_routing_keys` и содержит `inference_time`.
- **Детектор.** Это и делает `LinqDetector(transport="cloud")`. Каждый поток держит своё соединение и свою exclusive-очередь, пачка кадров публикуется целиком и собирается по message id. Настройки лежат в `worker.linq` в `config.yaml`, логин задаётся `LINQ_LOGIN`.
- **Работа со стендом.** `scripts/linq_stand.py` ходит в Jupyter: cookie-сессия хаба, загрузка и скачивание файлов, выполнение кода в ядре. VPN поднимается в отдельном контейнере (`docker/linq-vpn.Dockerfile`, `scripts/linq_stand.ps1`), сеть хоста не трогается.

Сверка TPU с квантованной симуляцией (`linq_bench.py`, эталон `ref_<m>.npz` от `linq_eval.py --ref`):

| ppe | значение |
|---|---|
| совпадение детекций TPU и симуляции | 1.0 |
| макс. разница скоров | 0.0 |
| совпадение детекций TPU и float32 | 0.696 (флипы около порогов) |

Скорость (ppe, 640×640, запуск из Jupyter стенда):

| что | время |
|---|---|
| TPU, `inference_time` из ответа | ~6.8 мс/кадр, под общей нагрузкой 40–46 мс |
| запрос в облако целиком | 120–285 мс |
| 4 запроса в полёте | 15–15.8 кадра/с |

Несколько камер (`scripts/linq_cams.py`): каждая камера даёт 6 кадров/с и работает в своём потоке со своей очередью.

| камер | p50 | p95 | всего кадров/с | ошибок |
|---|---|---|---|---|
| 1 | 122 мс | 134 мс | 6 | 0 |
| 2 | 161 мс | 186 мс | 12 | 0 |
| 4 | 179 мс | 200 мс | 22.2 | 0 |

### Raw-режим: пакуем int8 сами

С `raw=False` облако получает float32 и пакует его на своей стороне. Это медленно: на кадр уходит 4.9 МБ туда и ~370 КБ обратно, и весь вызов занимает ~1 с, хотя сам TPU отрабатывает за 40 мс. С `raw=True` облако берёт готовые uint8-блоки, а упаковку и распаковку делает `worker/app/detector/linq_raw.py`. Это чистый numpy, SDK для него не нужен.

- **Раскладка.** Берётся из `metadata.json` внутри `.tpu`: `user_shape/order`, `tpu_shape/order`, `scales`, `padding`, `ddr_word_len`.
  - Вход `images`: NCHW float → int8 `rint(x·127)`, каналы добиты до 4, лежит как HWC4. Итого (51200, 32) uint8, 1.6 МБ.
  - Выходы: (C/128, W, 1, 128) int8 или fp16, хвост 48 якорей обрезается, `·scale`. Итого 135 КБ.
- **Кадр сразу в int8.** `ImagePacker` превращает BGR uint8 после letterbox в raw одним `cv2.LUT` по RGBA (таблица на 256 значений, 4-й канал нулевой). Float-тензор не строится. 3.4 мс против 54 мс у пути «float-препроцесс + pack».
  - Воркер в raw зовёт `detector.prepare()` вместо `preprocess()` (`_infer_packed` в `worker/app/main.py`).
- **Сверка.** Эталон снят SDK-шным `tpu_io.pack/unpack` в контейнере `linq-sdk` на настоящей программе ppe. Совпадает бит в бит (`worker/tests/test_linq_raw.py`). На стенде: совпадение детекций с симуляцией 1.0, разница скоров 0.0.
- **Включение.** `worker.linq.raw: true` в `config.yaml` (уже включено) или `--raw` у `linq_bench.py`. Программа `.tpu` должна лежать локально в `models/<m>/linq/`, рядом с `meta.yaml`, и совпадать с той, что в `/auto/cache/<логин>/`. Сверять по `md5sum`.

`scripts/linq_probe.py` раскладывает вызов по этапам. Стенд 23.10 ~19:50 мск, CPU контейнера свободен:

| ppe, на кадр | prepare | облако | TPU (`inference_time`) | итого | кадров/с |
|---|---|---|---|---|---|
| raw=False, пачка 1 | — | 157 мс | 50 мс | 158 мс | 6.3 |
| raw=True, пачка 1 | 6.7 мс | 72 мс | 35 мс | 80 мс | 12.5 |
| raw=False, пачка 4 | — | 68 мс | 38 мс | 69 мс | 14.5 |
| raw=True, пачка 4 | 5.2 мс | 27 мс | 41 мс | 34 мс | 29 |
| raw=False, пачка 8 | — | 54 мс | 42 мс | 56 мс | 17.9 |
| raw=True, пачка 8 | 3.8 мс | 21 мс | 37 мс | 26 мс | 38 |

Пачка обрабатывается облаком параллельно, поэтому «облако на кадр» меньше `inference_time` одного кадра. Raw выигрывает в 2 раза на любой пачке.

То же 23.10 чуть раньше, когда CPU контейнера съедал забытый eval (см. ниже):

| ppe, на кадр | prepare | облако | из них TPU | итого |
|---|---|---|---|---|
| raw=False, пачка 1 | — | 1070 мс | 43 мс | 1080 мс |
| raw=True, пачка 1 | 172 мс | 208 мс | 35 мс | 390 мс |
| raw=False, пачка 4 | — | 1019 мс | 43 мс | 1044 мс |
| raw=True, пачка 4 | 144 мс | 143 мс | 40 мс | 306 мс |

### Ресурсы контейнера Jupyter

Контейнеру выдано **1.5 ядра** (`cpu.max 150000 100000`) и **4 ГБ** памяти, хотя `nproc` показывает 64. Всё, что крутится в нашем Jupyter, делит эти 1.5 ядра: ядра ноутбуков, мост, воркер, забытые eval.

- Перед замерами и демо смотреть `ps aux --sort=-%cpu` и `/sys/fs/cgroup/cpu.stat` (`nr_throttled`). 23.10 забытый `linq_eval.py` съедал 141% и душил всё остальное.
- На стенде не строить float-тензоры на весь кадр: `np.rint` по кадру при троттлинге занимает 220 мс.

### Воркер на стенде, веб у себя

VPN тянет около 0.6 МБ/с. Кадр 640×640 float32 весит 4.9 МБ, поэтому возить кадры с нашей машины на TPU нельзя: выходит около 9.5 с на кадр. Схема такая: воркер живёт на стенде, а backend, MediaMTX и UI остаются локально.

- **Мост** `scripts/linq_bridge.py`.
  - На стенде (`stand`) он притворяется бэком на `127.0.0.1:8000`. HTTP и WebSocket воркера уходят в очереди `<prefix>.req` и `<prefix>.ws` того же брокера.
  - Локально (`local`) мост забирает их из очередей и отдаёт настоящему бэку. `X-Internal-Token` подставляется только локально.
  - Через мост ходят рамки и события, это килобайты.
- **Видео.** Ролики лежат на обеих сторонах, а воспроизведение синхронизировано по часам (`shared/sync.py`).
  - Локальный мост переписывает `rtsp_url` файловых камер на `syncfile://<имя>`. Воркер читает файл с того же смещения `(now - SYNC_EPOCH_S) % duration`, с которого его публикует backend.
  - Кадр в UI совпадает с кадром, на котором считались рамки.
- **Режим HLS.** Для просмотра через прокси без WebRTC есть `VIDEO_MODE=hls`, ограничить поток можно через `STREAM_WIDTH` и `STREAM_MAXRATE`.

## Что учесть в сервисе

- **Батч.** Программы скомпилированы с batch 1. В облачном режиме `batch_limit` равен `worker.linq.batch`: столько кадров публикуется сразу, и TPU обрабатывает их параллельно. С `pytpu` по одному кадру.
- **Пороги.** Пороги по классам в `model.yaml` подобраны для float-модели. mAP int8 не упал, но детекции около порогов расходятся с float (0.696), поэтому пороги стоит подобрать заново под int8.
- **Вырезки.** Сигарета ищется моделью `hazard` на вырезках людей. Если TPU медленнее видеокарты, первым делом уменьшить `crop_pass.cigarette.max_per_tick`.
- **Процессор.** На onnxruntime узкое место — однопоточный главный цикл воркера (декодирование, предобработка, правила), а не инференс. С TPU это так и остаётся.
