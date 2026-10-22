@echo off
rem Ночной прогон: проверка окружения, ppe, затем hazard. Следить: python status.py --watch
cd /d "%~dp0"
python run_all.py %*
