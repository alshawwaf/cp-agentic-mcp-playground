#!/bin/bash
set -e

echo ">> Pulling latest code..."
git pull

echo ">> Rebuilding n8n (forcing latest version)..."
# --pull: checks for newer base image
# --no-cache: rebuilds the image layer from scratch
docker compose build --pull --no-cache n8n

echo ">> Starting services..."
docker compose up -d

echo ">> Done! System is updated."
