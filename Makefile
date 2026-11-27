.PHONY: subtitulos

subtitulos:
	@if [ -x .venv/bin/python ]; then \
		.venv/bin/python tools/subtitle_ui.py; \
	elif [ -x .venv-linux/bin/python ]; then \
		.venv-linux/bin/python tools/subtitle_ui.py; \
	else \
		echo "No se encontró el entorno virtual. Sigue INSTALACION.md para crearlo e instalar las dependencias."; \
		exit 1; \
	fi
