# training

Обучение детекторов ppe, hazard и heads. Базовая модель — `yolov8s.pt` (ultralytics), экспорт в onnx (opset 12, вход 640).
Датасеты, склейки и прогоны в git не входят (`datasets/`, `merged/`, `runs/` в `training/.gitignore`). Метрики готовых моделей — [docs/METRICS.md](../docs/METRICS.md).

## Модели и данные

| модель | классы | источники (configs/*.yaml) |
|---|---|---|
| ppe | person, helmet, no_helmet, vest, mask, no_mask, vehicle | Construction Site Safety (Roboflow, v27), SH17 (Kaggle) |
| hazard | fire, smoke, cigarette | D-Fire (Kaggle, не больше 8000 картинок), smoker-yolo (Roboflow) |
| heads | helmet, no_helmet | Hard Hats, Construction Site Safety |
| heads2 | helmet, no_helmet | как heads + головы рабочих из SH17 как no_helmet (баланс ~33k / 23k) |
| people | person, car, motorcycle, bus, truck, cell phone | не обучается: COCO yolov8s, голова урезана с 80 до 6 классов, `scripts/make_people_model.py` |
| pose | 17 ключевых точек человека (COCO) | не обучается: yolov8n-pose, `scripts/make_pose_model.py`; падение, «лежит», переноска груза, телефон у уха |

- В SH17 нет классов «без каски» / «без маски»: `no_helmet` выводится из головы без пересечения с каской, `no_mask` — из лица без маски/щитка (правило `derive` в конфиге).
- heads2 появилась, потому что v1 без голов из SH17 принимала кепки и волосы за каску.
- Val — 10% (`val_fraction: 0.1`), у SH17 — его собственные списки train/val.

## Конвейер

| файл | что делает |
|---|---|
| `download.py` | качает датасеты: Roboflow (`ROBOFLOW_API_KEY`) и Kaggle (`~/.kaggle/kaggle.json`) в `datasets/` |
| `coco2yolo.py` | COCO-экспорт Roboflow (Hard Hats) → раскладка YOLO для merge.py |
| `merge.py` | склейка источников по конфигу: маппинг классов, `derive`, split → `merged/<модель>/data.yaml` |
| `preflight.py` | проверка окружения (CUDA, пакеты, место на диске, merged) перед обучением |
| `train.py` | обучение одной модели, экспорт `best.onnx` |
| `run_all.py`, `run_all.bat` | ночной прогон: preflight, затем модели по очереди (по умолчанию ppe и hazard), журнал в `logs/run_all.log` |
| `status.py` | прогресс обучения, `--watch` |
| `evaluate.py` | val по классам, подбор порогов, пишет `runs/<модель>/model.yaml` и `thresholds.md`, для ppe — метрики по источникам |
| `eval_second_pass.py` | проверка второго прохода по головам (кроп + повторная детекция) на val |

## Гиперпараметры

`train.py`: yolov8s, 80 эпох, batch 16, imgsz 640, patience 20, mosaic 1.0 (выключается за 10 эпох до конца), fliplr 0.5, scale 0.5. Всё, кроме patience и аугментаций, меняется флагами (`--epochs`, `--batch`, `--imgsz`, `--scale`, `--fraction`, `--resume`).

## Запуск

```
cd training
pip install -r requirements.txt
python download.py
python merge.py configs/ppe.yaml
python run_all.py ppe hazard
python evaluate.py ppe hazard
```

Результат — `training/runs/<модель>/weights/best.onnx` и `model.yaml`. В `models/` переносит `scripts/sync_models.ps1`.
