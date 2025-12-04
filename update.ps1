Write-Host ">> Pulling latest code..."
git pull

Write-Host ">> Rebuilding n8n (forcing latest version)..."
# --pull: checks for newer base image
# --no-cache: rebuilds the image layer from scratch
docker compose build --pull --no-cache n8n

Write-Host ">> Starting services..."
docker compose up -d

Write-Host ">> Done! System is updated."
