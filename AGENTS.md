# Agent Notes

This repository contains the backend for an ePaper photo frame running on AWS SAM.

## Core Concepts
- **Stack**: `photo-frame-backend` (default) in `eu-west-1`
- **Functions**: `ProcessImageFunction`, `GetUploadUrlFunction`, `GetRandomImageFunction`
- **Storage**: `photo-frame-<env>-<account>` S3 + `photo-frame-images-<env>` DynamoDB
- **Custom Domain**: API Gateway custom domain is optional and controlled by `EnableCustomDomain`

## Local Development
- Use **uv** for Python tooling:
  - `uv venv --python 3.12.0`
  - `uv pip install -r requirements-dev.txt`
- Run tests:
  - `uv run pytest`
  - Coverage: `uv run pytest --cov=src --cov-report=term-missing`
  - Type checking: `uv run mypy src tests`

## Deployment
- Local config lives in `samconfig.toml` (gitignored).
- Deploy:
  - `sam build`
  - `sam deploy --no-confirm-changeset --no-fail-on-empty-changeset`

## Logging
- Lambda logs: `/aws/lambda/photo-frame-<function>-dev`
- API Gateway access logs (if enabled): `/aws/api-gateway/photo-frame-<env>`

## Image Pipeline
- Palette is loaded from `PALETTE_PATH` (defaults to `palette.json` in the Lambda package).
- Calibration script: `scripts/calibrate_palette.py`

## Notes for Agents
- Avoid committing secrets (API keys, `.env`, `samconfig.toml`).
- Keep API Gateway usage plans (StageKeys are deprecated).
- The repo uses GPL-3.0-only.
