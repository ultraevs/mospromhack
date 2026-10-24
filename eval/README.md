# Разметка для метрик

Эталон, по которому считаются цифры в `docs/LINQ.md`. Размечал человек глазами по кадрам роликов из `data/videos`, автоматических меток нет.

- `event_gt/*.yaml` — интервалы нарушений по роликам (тип, начало, конец, спорные помечены). Считает `scripts/event_eval.py`: события воркера (`event_gt/runs/events_*.json`, прогоны на стенде; по умолчанию итоговый `events_final*`) против интервалов, итог TP/FP/FN по типам.
- `heads_gt/` — эталон касок на 65 людях: `labels.txt` (h — каска, n — без каски, ? — не разобрать), `items.json` (ролик, кадр, рамка), листы `sheet_*.jpg` с номерами, по которым размечали. Считает `python scripts/heads_gt.py eval` (гонит модели). `make` пишет новые листы в `data/heads_gt`, `eval` читает отсюда.
- Результаты: `docs/LINQ.md`, разделы про событийную метрику и эталон голов.
- `models/` — сырые цифры моделей: `metrics_full.json` (вся валидация, P/R/F1, mAP с 95% ДИ), `metrics.json` (int8 против float32), `stills_recall.json` (стоп-кадры). Считают `scripts/model_metrics.py` и `scripts/stills_recall.py`.
- Сами ролики в репозиторий не входят (`data/` в .gitignore). Отчёт `event_eval.py` пишет в `eval/event_gt/report.json`.

## Абляция

[event_gt/ablation.md](event_gt/ablation.md) — вклад каждой части пайплайна на тех же интервалах (float32, ONNX Runtime, 23 нарушения): только ppe, + модель голов, + плитки, без цвета жилета, пороги float по max F1. Главное: модель голов даёт +4 из 9 по каскам; пороги ppe/heads с val дают 15/23 при 0 ложных. Отчёты `event_gt/report_ablation_<X>.json`.

## Отложенный набор

[holdout/README.md](holdout/README.md) — 3 чужих ролика (145 с), на которых ничего не настраивали; разметка глазами до прогона по листам `holdout/sheets/`. Прогон `python scripts/holdout_eval.py`, итог в `holdout/report_local.json`. Проверяет ложные срабатывания на чистом видео: 0 ложных; единственный размеченный случай (лежащий в повёрнутом кадре) не пойман.
