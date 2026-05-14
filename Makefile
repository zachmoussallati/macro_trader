.PHONY: setup dev-backend dev-frontend dev-dagster dev-all test test-backend test-frontend lint format db-up db-down db-reset

setup:
	python make.py setup
dev-backend:
	python make.py dev-backend
dev-frontend:
	python make.py dev-frontend
dev-dagster:
	python make.py dev-dagster
dev-all:
	python make.py dev-all
test:
	python make.py test
test-backend:
	python make.py test-backend
test-frontend:
	python make.py test-frontend
lint:
	python make.py lint
format:
	python make.py format
db-up:
	python make.py db-up
db-down:
	python make.py db-down
db-reset:
	python make.py db-reset
