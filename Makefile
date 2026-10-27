.PHONY: install install-dev pipeline dashboard narrative test clean

install:            ## runtime dependencies
	pip install -r requirements.txt

install-dev:        ## runtime + Claude SDK + pytest
	pip install -r requirements-dev.txt

pipeline:           ## all stages: plan (reused) -> runs -> validate -> analyse -> narrate -> dashboard
	python run_pipeline.py

dashboard:          ## rebuild dashboard/index.html from the current data
	python run_pipeline.py --stage dashboard

narrative:          ## ask Claude for the narrative (needs ANTHROPIC_API_KEY), then rebuild the dashboard
	python run_pipeline.py --stage narrate --narrative claude && python run_pipeline.py --stage dashboard

test:               ## unit test suite
	pytest

clean:              ## remove caches and local build output
	rm -rf public .vercel .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
