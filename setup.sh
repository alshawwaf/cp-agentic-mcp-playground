#!/usr/bin/env bash
set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Check Point MCP Servers Starting Kit Setup ===${NC}"

# 1. Check Prerequisites
echo -e "\n${YELLOW}[1/4] Checking prerequisites...${NC}"

if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}Error: 'docker' is not installed or not in PATH.${NC}"
    echo "Please install Docker Engine and try again."
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    echo -e "${RED}Error: 'docker compose' is not available.${NC}"
    echo "Please install Docker Compose v2 and try again."
    exit 1
fi

echo -e "${GREEN}Docker and Docker Compose are available.${NC}"

# 2. Generate .env file
echo -e "\n${YELLOW}[2/4] Configuring environment...${NC}"

ENV_FILE=".env"
EXAMPLE_FILE=".env-example"

if [ -f "$ENV_FILE" ]; then
    echo -e "${YELLOW}A '$ENV_FILE' file already exists.${NC}"
    read -p "Do you want to overwrite it? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Skipping .env generation."
    else
        echo "Backing up existing .env to .env.bak"
        cp "$ENV_FILE" "$ENV_FILE.bak"
        rm "$ENV_FILE"
    fi
fi

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$EXAMPLE_FILE" ]; then
        echo "Creating $ENV_FILE from $EXAMPLE_FILE..."
        cp "$EXAMPLE_FILE" "$ENV_FILE"
    else
        echo -e "${RED}Error: $EXAMPLE_FILE not found!${NC}"
        exit 1
    fi
    
    echo -e "\n${YELLOW}Do you want to generate secure random passwords?${NC}"
    echo "If you are setting up a demo lab and want to use default credentials, choose 'No'."
    read -p "Generate random passwords? (y/N) " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Generating secure passwords..."
        
        # Helper to generate random strings
        generate_secret() {
            openssl rand -hex 16
        }
        
        POSTGRES_PASSWORD=$(generate_secret)
        N8N_ENCRYPTION_KEY=$(generate_secret)
        N8N_JWT_SECRET=$(generate_secret)
        N8N_ADMIN_PASSWORD=$(generate_secret)
        N8N_BASIC_AUTH_PASSWORD=$(generate_secret)
        
        # Use sed to replace placeholders or keys
        # We assume the keys exist in .env-example
        
        sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PASSWORD}|" "$ENV_FILE"
        sed -i "s|^N8N_ENCRYPTION_KEY=.*|N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY}|" "$ENV_FILE"
        sed -i "s|^N8N_USER_MANAGEMENT_JWT_SECRET=.*|N8N_USER_MANAGEMENT_JWT_SECRET=${N8N_JWT_SECRET}|" "$ENV_FILE"
        sed -i "s|^N8N_ADMIN_PASSWORD=.*|N8N_ADMIN_PASSWORD=${N8N_ADMIN_PASSWORD}|" "$ENV_FILE"
        sed -i "s|^N8N_BASIC_AUTH_PASSWORD=.*|N8N_BASIC_AUTH_PASSWORD=${N8N_BASIC_AUTH_PASSWORD}|" "$ENV_FILE"
        
        echo -e "${GREEN}.env file updated with secure passwords.${NC}"
        echo -e "${YELLOW}IMPORTANT: The generated passwords are in .env. Please review them.${NC}"
    else
        echo "Using default values from .env-example."
        echo "Please edit .env manually to set your passwords."
    fi
fi

# 3. Optional API Keys
echo -e "\n${YELLOW}[3/4] Optional Configuration${NC}"
echo "You can edit the .env file manually to add your Check Point API keys."
echo "Or you can do it now."
read -p "Do you want to enter API keys now? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    read -p "Enter Management Host IP (e.g. 1.2.3.4): " MGMT_HOST
    if [ ! -z "$MGMT_HOST" ]; then
        sed -i "s|^MANAGEMENT_HOST=.*|MANAGEMENT_HOST=$MGMT_HOST|" "$ENV_FILE"
    fi
    
    read -p "Enter SMS API Key: " SMS_KEY
    if [ ! -z "$SMS_KEY" ]; then
        # Escape special chars for sed
        ESCAPED_KEY=$(printf '%s\n' "$SMS_KEY" | sed -e 's/[\/&]/\\&/g')
        sed -i "s|^SMS_API_KEY=.*|SMS_API_KEY=$ESCAPED_KEY|" "$ENV_FILE"
    fi
    
    echo -e "${GREEN}API keys updated.${NC}"
fi

# 4. Final Instructions
echo -e "\n${YELLOW}[4/4] Setup Complete!${NC}"
echo "To start the stack (CPU profile):"
echo -e "  ${GREEN}docker compose --profile cpu up -d${NC}"
echo ""
echo "To start with GPU support (NVIDIA):"
echo -e "  ${GREEN}docker compose --profile gpu-nvidia up -d${NC}"
echo ""
echo "Access n8n at: http://localhost:5678"
echo "Access Open WebUI at: http://localhost:3000"
