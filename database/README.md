# Database Setup

This directory contains the initial database schema and seed data for ResyncBot.

## Quick Setup

### Using PostgreSQL:
```bash
# Create a new database
createdb resyncbot

# Import the schema and data
psql -d resyncbot -f database/resyncbot_init.sql
# Use your DATABASE_URL from .env
psql "your_database_url_here" -f database/resyncbot_init.sql
DATABASE_URL=postgresql://username:password@localhost:5432/resyncbot
## Step 3: Update your main README.md

Add this section to your README (insert after installation steps):
````markdown
### 4. Set up the database

**PostgreSQL** is required for ResyncBot.

#### Install PostgreSQL:
- **macOS**: `brew install postgresql && brew services start postgresql`
- **Ubuntu/Debian**: `sudo apt install postgresql postgresql-contrib`
- **Windows**: Download from [postgresql.org](https://www.postgresql.org/download/windows/)

#### Create and initialize the database:
```bash
# Create database
createdb resyncbot

# Import schema and seed data (includes 10,000 tracks)
psql -d resyncbot -f database/resyncbot_init.sql

## Step 4: Check/update your `.gitignore`

Make sure you have:
```bash
# Add to .gitignore if not already there
cat >> .gitignore << 'EOF'

# Environment variables
.env
.env.local
*.env

# Database backups (keep only the init file)
database/*.sql
!database/resyncbot_init.sql
*.backup
*.dump

# Python
__pycache__/
*.py[cod]
*$py.class
venv/
env/
.venv/

# IDE
.vscode/
.idea/
*.swp

# OS
.DS_Store
Thumbs.db
