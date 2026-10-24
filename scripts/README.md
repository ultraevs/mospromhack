# scripts

Всё, что запускается руками. Python-скрипты гоняются из корня репозитория (`python scripts/<имя>.py --help`), ps1 — из PowerShell.

## Запуск сервиса

| скрипт | что делает |
|---|---|
| `setup.ps1` | создаёт `.venv` (Python 3.12), ставит зависимости |
| `dev.ps1` | mediamtx + backend + worker + frontend, логи в `logs\`, Ctrl+C гасит всё |
| `demo.ps1` | запуск демо одной командой (`-Docker`, `-Cpu`, `-MaxCameras`) |
| `stop.ps1` | гасит процессы из `logs\dev.pids`, если окно dev.ps1 закрыли крестиком |
| `sync_models.ps1` | копирует `best.onnx` и `model.yaml` ppe/hazard из папки обучения (`TRAIN_RUNS_DIR`) в `models/` |
| `seed_demo.py` | демо-история для аналитики: демо-камеры и события за N дней, `--clear` убирает |
| `webhook_receiver.py` | локальный приёмник вебхуков для проверки уведомлений |

## Оценка качества

| скрипт | что делает |
|---|---|
| `event_eval.py` | событийная метрика: события воркера против интервалов из `eval/event_gt/*.yaml`, TP/FP/FN по типам |
| `holdout_eval.py` | прогон прод-конфига по отложенному набору `eval/holdout` + `event_eval` |
| `model_metrics.py` | P/R/F1 при рабочем пороге, mAP50, mAP50-95; float32 (onnx) и int8 (симуляция SDK), бутстрап-CI |
| `eval_metrics.py` | P/R/mAP по классам на val, float32 onnx на CPU |
| `eval_map.py` | mAP50 по классам для onnx-моделей на val |
| `stills_recall.py` | стоп-кадры как у жюри: весь пайплайн воркера на картинке, recall и ложные при порогах x1.0/x0.8/x0.6 |
| `heads_gt.py` | эталон «каска / без каски» на наших роликах: `make` — листы для разметки, `eval` — сверка моделей |
| `float_thresholds.py` | пороги float по max F1 на val, только печать/json |
| `video_report.py` | размер людей и голов на наших роликах, вклад второго прохода по головам, ложные hazard |
| `bench.py` | N копий одного видео как N камер — сколько вывозит машина |
| `profile_stages.py` | время по этапам обработки (декод, детекция, правила) на N камерах |
| `make_test_video.py` | ролик из картинок датасета (каждая держится `--seconds`) |

## LinQ TPU

| скрипт | что делает |
|---|---|
| `linq_calib.py` | набор калибровки для int8: картинки датасета + кадры роликов |
| `linq_compile.py` | onnx → программа TPU: режет хвост модели на CPU, квантует, компилирует |
| `linq_thresholds.py` | пороги под int8 по max F1 (симуляция = TPU), пишет в `models/<m>/linq/meta.yaml` |
| `linq_eval.py` | AP50 float32 против int8 на val |
| `linq_remote.py` | калибровка/компиляция/eval на стенде: `setup`, `sync`, `sh` и т.д., у себя ничего не считаем |
| `linq_stand.py` | клиент Jupyter на стенде: выполнить код/shell, загрузить файл |
| `linq_stand.ps1` | то же через Docker с VPN |
| `linq_vpn.ps1` | контейнер с VPN до стенда: `up`/`down`/`exec`/`status` |
| `linq_sdk.ps1` | Docker-образ с SDK LinQ и запуск команд в нём |
| `linq_pack.py` | архив кода воркера и моделей для стенда |
| `linq_deploy.py` | воркер с TPU + мост на стенде, backend/UI у себя (см. docs/LINQ.md) |
| `linq_bridge.py` | мост воркер ↔ backend через RabbitMQ |
| `linq_pipeline.py` | весь воркер на стенде без backend: ролики → TPU → правила → события; `--once` для метрик |
| `linq_relay.py` | посредник на стенде: jpeg по VPN → облачный TPU → рамки json |
| `linq_bench.py` | задержка и совпадение с onnx для моделей на TPU |
| `linq_cams.py` | сколько камер выдерживает программа на TPU при заданном fps |
| `linq_heads_check.py` | сверка модели голов на TPU и onnx на одних кадрах |
| `linq_warmup.py` | прогрев TPU перед защитой (после простоя первый вызов медленный) |

## Подготовка моделей

| скрипт | что делает |
|---|---|
| `make_people_model.py` | модель people: COCO yolov8s, голова урезана с 80 до 6 классов (person, car, motorcycle, bus, truck, cell phone) |
| `make_pose_model.py` | модель pose: yolov8n-pose (17 точек) в `models/pose/` |
| `export_dynamic.py` | экспорт `best.dynamic.onnx` с динамическим размером входа |
| `make_fp16.py` | `best.dynamic.onnx` → `best.fp16.onnx` |

## Замеры по ходу работы

Одноразовые исследовательские скрипты. Цифры из них вошли в docs/LINQ.md, повторно запускать не нужно.

| скрипт | что делает |
|---|---|

## Главное

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -File scripts\dev.ps1
python scripts/event_eval.py
python scripts/holdout_eval.py
python scripts/model_metrics.py --limit 0 --float-only --boot 1000 --out data/linq/metrics_full.json
python scripts/stills_recall.py --help
```
