PY ?= python

.PHONY: docs test starter full repro

docs:
	$(PY) -m cartrecovery.docs

test:
	$(PY) -m pytest -q

starter:
	$(PY) -m cartrecovery.services.pipeline --scale 0.05 --out output/starter --validate

full:
	$(PY) -m cartrecovery.services.pipeline --scale 1 --out output/full_scale --validate

repro:
	$(PY) -m cartrecovery.services.pipeline --scale 0.05 --out output/starter --validate --repro-check
