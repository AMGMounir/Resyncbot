# Contributing to ResyncBot

Thank you for your interest in contributing to ResyncBot! I've worked on this project for a very long time so I appreciate any help I receive from the developer community.

## How to Contribute

### Reporting Bugs

If you find a bug, please open an issue with the following information:

- **Clear description** of the bug
- **Steps to reproduce** the issue
- **Expected behavior** vs **actual behavior**
- **Environment details**:
  - Operating System
  - Python version
  - PostgreSQL version
  - Relevant error messages or logs

**Example:**
```
Bug: Bot crashes when using /resync command

Steps to reproduce:
1. Run bot with main.py
2. Use /resync command in Discord
3. Bot crashes with error XYZ

Expected: Command should execute successfully
Actual: Bot crashes

Environment: Windows 11, Python 3.11, PostgreSQL 15
Error: [paste error message here]
```

### Suggesting Features

We love new ideas! To suggest a feature:

1. Open an issue with the "Feature Request" label
2. Describe the feature and why it would be useful
3. Explain how it might work
4. Include examples or mockups if applicable

### Contributing Code

#### Getting Started

1. **Fork the repository**
   ```bash
   # Click the "Fork" button on GitHub
   ```

2. **Clone your fork**
   ```bash
   git clone https://github.com/YOUR_USERNAME/resyncbot.git
   cd resyncbot
   ```

3. **Set up your development environment**
   ```bash
   # Create virtual environment
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   
   # Install dependencies
   pip install -r requirements.txt
   
   # Set up database
   createdb resyncbot
   psql -d resyncbot -f database/resyncbot_init.sql
   
   # Copy and configure .env
   cp .env.example .env
   # Edit .env with your tokens
   ```

4. **Create a new branch** for your feature
   ```bash
   git checkout -b feature/your-feature-name
   ```

#### Making Changes

- **Write clean code**: Follow existing code style and conventions
- **Comment complex logic**: Help others understand your code
- **Test your changes**: Make sure everything works before submitting
- **Keep commits focused**: One feature or fix per commit

**Good commit messages:**
```
Add: support for YouTube links in resync command
Fix: database connection timeout issue
Update: improve error handling in API calls
Refactor: simplify track metadata parsing
```

#### Code Style Guidelines

- Use meaningful variable and function names
- Follow PEP 8 style guide for Python code
- Add docstrings to functions and classes
- Keep functions small and focused on a single task
- Handle errors gracefully with try/except blocks

**Example:**
```python
async def fetch_track_metadata(track_id: str) -> dict:
    """
    Fetch metadata for a specific track from the database.
    
    Args:
        track_id: The unique identifier for the track
        
    Returns:
        dict: Track metadata including title, artist, duration, etc.
        
    Raises:
        TrackNotFoundError: If track_id doesn't exist in database
    """
    try:
        # Implementation here
        pass
    except Exception as e:
        logger.error(f"Failed to fetch track {track_id}: {e}")
        raise
```

#### Testing Your Changes

Before submitting a pull request:

1. **Test locally**
   - Run both `resync_api.py` and `main.py`
   - Test the specific feature you added/modified
   - Test existing features to ensure nothing broke

2. **Check for errors**
   - Look for any error messages in console
   - Verify database operations work correctly
   - Test with different inputs (edge cases)

3. **Test on a Discord server**
   - Invite your development bot to a test server
   - Try all affected commands
   - Verify bot responds correctly

#### Submitting a Pull Request

1. **Push your changes** to your fork
   ```bash
   git add .
   git commit -m "Add: brief description of your changes"
   git push origin feature/your-feature-name
   ```

2. **Open a Pull Request** on GitHub
   - Go to the original ResyncBot repository
   - Click "New Pull Request"
   - Select your fork and branch
   - Fill out the PR template

3. **PR Description should include:**
   - What changes you made
   - Why you made them
   - How to test the changes
   - Any relevant issue numbers (e.g., "Fixes #123")

**Example PR description:**
```
## Changes
- Added support for Apple Music links in resync command
- Updated track metadata parser to handle new format

## Why
Users requested Apple Music support (issue #45)

## Testing
1. Use /resync command with Apple Music link
2. Verify track metadata is extracted correctly
3. Check that resync completes successfully

Fixes #45
```

### What to Contribute

Here are some ideas to help give an idea on how you can contribute:

- **Bug fixes**: Check open issues labeled "bug"
- **New features**: Check issues labeled "enhancement"
- **Documentation**: Improve README, add code comments, write guides
- **UI/UX**: Improve bot responses, error messages, or command formatting
- **Testing**: Add test cases, improve error handling
- **Refactoring**: Improve code quality, performance, or organization

### Need Help?

- Check existing issues and pull requests first
- Open an issue if you have questions
- Be specific about what you need help with
- Include relevant code snippets or error messages

## Development Tips

### Running in Debug Mode

Enable debug mode in `.env` for verbose logging:
```env
DEBUG_MODE=true
```

### Database Changes

If you modify the database schema:
1. Update `database/resyncbot_init.sql`
2. Document changes in `database/README.md`
3. Include migration instructions in your PR

### Working with the API

The ResyncBot API (`resync_api.py`) runs separately from the bot. When developing:
- Keep API endpoints RESTful
- Document new endpoints
- Handle errors gracefully
- Validate input data

### Common Issues

**Bot not responding:**
- Check both `main.py` and `resync_api.py` are running
- Verify bot token is correct
- Ensure Message Content Intent is enabled

**Database errors:**
- Check PostgreSQL is running
- Verify DATABASE_URL is correct
- Ensure database is initialized

**Import errors:**
- Activate virtual environment
- Install all requirements: `pip install -r requirements.txt`

## Questions?

Don't hesitate to ask! Open an issue with the "question" label or comment on relevant issues/PRs.

Thank you for contributing to ResyncBot! :D