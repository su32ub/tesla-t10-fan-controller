.PHONY: test check run deb desktop

test:
	python3 -m unittest discover -v

check:
	python3 -m gpu_fan_controller --check --config config.example.json

run:
	python3 -m gpu_fan_controller.desktop --config config.json --dry-run

desktop:
	./deploy/run-desktop.sh

deb:
	./packaging/build_deb.sh
