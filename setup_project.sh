#!/bin/bash

# setup_project.sh - Easy setup for InferiaLLM

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}==============================================${NC}"
echo -e "${BLUE}       InferiaLLM Project Setup Helper        ${NC}"
echo -e "${BLUE}==============================================${NC}"

# 1. Environment Setup
echo -e "\n${YELLOW}[1/4] Checking Environment Configuration...${NC}"

if [ -f ".env" ]; then
    echo -e "${GREEN}✓ .env file already exists.${NC}"
    
    # Check for default secrets
    if grep -q "dev-secret-key-change-in-production" .env || grep -q "dev-internal-key-change-in-prod" .env; then
        echo -e "${YELLOW}WARNING: Default insecure keys detected in .env!${NC}"
        read -p "Do you want to generate secure keys? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            ROTATE_KEYS=true
        fi
    fi
else
    echo -e "${YELLOW}Creating .env file from .env.sample...${NC}"
    cp .env.sample .env
    ROTATE_KEYS=true
fi

if [ "$ROTATE_KEYS" = true ]; then
    # Generate secrets
    echo -e "${BLUE}Generating secure keys for .env...${NC}"
    
    # Function to generate random hex string
    generate_secret() {
        python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    }
    
    JWT_SECRET=$(generate_secret)
    INTERNAL_KEY=$(generate_secret)
    # SUPERADMIN_PASS="admin123" # User can change this manually
    
    # Use sed to replace the placeholders
    if [[ "$OSTYPE" == "darwin"* ]]; then
        SED_CMD="sed -i ''"
    else
        SED_CMD="sed -i"
    fi
    
    # Replace default or existing keys if we decided to rotate
    # We look for the key definition and replace the whole line
    $SED_CMD "s/JWT_SECRET_KEY=.*/JWT_SECRET_KEY=\"$JWT_SECRET\"/" .env
    $SED_CMD "s/INTERNAL_API_KEY=.*/INTERNAL_API_KEY=\"$INTERNAL_KEY\"/" .env
    
    echo -e "${GREEN}✓ Updated .env with secure JWT_SECRET_KEY and INTERNAL_API_KEY.${NC}"
fi

# Check for SUPERADMIN_EMAIL configuration
# If .env exists but doesn't have SUPERADMIN_EMAIL (old version), add it
if ! grep -q "SUPERADMIN_EMAIL" .env; then
    echo >> .env # ensure newline
    echo 'SUPERADMIN_EMAIL="admin@example.com"' >> .env
    echo -e "${GREEN}✓ Added SUPERADMIN_EMAIL to .env.${NC}"
fi

# Optional: Prompt to set superadmin email if it's still default
if grep -q "admin@example.com" .env; then
    echo -e "${YELLOW}Default Superadmin Email (admin@example.com) detected.${NC}"
    read -p "Do you want to change it? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        read -p "Enter new Superadmin Email: " NEW_ADMIN_EMAIL
        if [[ -n "$NEW_ADMIN_EMAIL" ]]; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s/SUPERADMIN_EMAIL=.*/SUPERADMIN_EMAIL=\"$NEW_ADMIN_EMAIL\"/" .env
            else
                sed -i "s/SUPERADMIN_EMAIL=.*/SUPERADMIN_EMAIL=\"$NEW_ADMIN_EMAIL\"/" .env
            fi
            echo -e "${GREEN}✓ SUPERADMIN_EMAIL updated to $NEW_ADMIN_EMAIL.${NC}"
        fi
    fi
fi

# 2. Virtual Environment
echo -e "\n${YELLOW}[2/4] Setting up Virtual Environment...${NC}"

if [ -d ".venv" ]; then
    echo -e "${GREEN}✓ Virtual environment (.venv) already exists.${NC}"
else
    echo -e "Creating virtual environment..."
    python3 -m venv .venv
    echo -e "${GREEN}✓ Created .venv.${NC}"
fi

# Activate venv
source .venv/bin/activate
echo -e "${BLUE}Activated virtual environment.${NC}"

# 3. Install Dependencies
echo -e "\n${YELLOW}[3/4] Installing Dependencies...${NC}"
read -p "Do you want to install/update dependencies? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "Upgrading pip..."
    pip install --upgrade pip
    
    echo -e "Installing package in editable mode..."
    # Check if we are checking root or package dir.
    if [ -f "package/pyproject.toml" ]; then
        pip install -e package
        # Optional: install dev dependencies
        # pip install -e "package[dev,db,ml]"
    elif [ -f "pyproject.toml" ]; then
        pip install -e .
    else
        echo -e "${RED}Could not find pyproject.toml in root or package/. Skipping install.${NC}"
    fi
    echo -e "${GREEN}✓ Dependencies installed.${NC}"
else
    echo -e "Skipping dependency installation."
fi

# 4. Initialization
echo -e "\n${YELLOW}[4/4] Initializing Database...${NC}"
echo -e "This requires Docker (Postgres/Redis) to be running if using default settings."
read -p "Do you want to run 'inferiallm init'? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo -e "Running inferiallm init..."
    inferiallm init
    echo -e "${GREEN}✓ Initialization complete.${NC}"
else
    echo -e "Skipping initialization."
fi

echo -e "\n${BLUE}==============================================${NC}"
echo -e "${GREEN}Setup Complete!${NC}"
echo -e "To activate the environment in the future, run: ${YELLOW}source .venv/bin/activate${NC}"
echo -e "To start the services, run: ${YELLOW}inferiallm start${NC}"
echo -e "${BLUE}==============================================${NC}"
