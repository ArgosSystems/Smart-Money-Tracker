# Contributing to Smart Money Tracker

First off, thank you for considering contributing to Smart Money Tracker! It's people like you that make this project a great tool for the crypto community.

---

## 📜 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Feature Requests](#feature-requests)

---

## 🤝 Code of Conduct

This project and everyone participating in it is governed by our Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

### Our Standards

- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on what is best for the community
- Show empathy towards other community members
- Accept constructive criticism gracefully

---

## 🔧 How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check the existing issues to avoid duplicates. When you create a bug report, include as many details as possible:

- **Use a clear and descriptive title**
- **Describe the exact steps to reproduce the problem**
- **Provide specific examples** (logs, screenshots, etc.)
- **Describe the behavior you observed and expected**
- **Include your environment details** (OS, Python version, etc.)

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion:

- **Use a clear and descriptive title**
- **Provide a step-by-step description of the suggested enhancement**
- **Explain why this enhancement would be useful**
- **Include screenshots or mockups if applicable**

### Pull Requests

- Fill in the required template
- Do not include issue numbers in the PR title
- Follow the coding standards
- Include tests for new functionality
- Update documentation as needed

---

## 💻 Development Setup

### Prerequisites

- Python 3.11 or higher
- Git
- An Alchemy account (free tier works)

### Setting Up Your Development Environment

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/smart-money-tracker.git
   cd smart-money-tracker
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt  # If available
   ```

4. **Set up pre-commit hooks** (optional but recommended)
   ```bash
   pip install pre-commit
   pre-commit install
   ```

5. **Create a `.env` file**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

6. **Run the application**
   ```bash
   python start.py
   ```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=api --cov=bots --cov-report=html

# Run specific test file
pytest tests/test_whale_tracker.py -v
```

### Code Quality Tools

```bash
# Format code with Black
black .

# Sort imports with isort
isort .

# Lint with Ruff
ruff check .

# Type check with MyPy
mypy .
```

---

## 📏 Coding Standards

### Python Style Guide

- Follow [PEP 8](https://pep8.org/) conventions
- Use **Black** for code formatting (line length: 100)
- Use **isort** for import sorting
- Write docstrings for all public modules, functions, classes, and methods

### Code Organization

```
smart-money-tracker/
├── api/                 # FastAPI backend
│   ├── routers/         # API endpoints
│   └── services/        # Business logic
├── bots/                # Bot implementations
│   ├── discord_bot/     # Discord-specific code
│   └── telegram_bot/    # Telegram-specific code
└── config/              # Configuration
```

### Documentation Strings

Use Google-style docstrings:

```python
def get_token_price(token_address: str) -> float:
    """Return current USD price of an ERC-20 token via CoinGecko.
    
    Args:
        token_address: The Ethereum address of the token contract.
        
    Returns:
        The current USD price, or 0.0 if the token is unknown.
        
    Raises:
        ValueError: If token_address is not a valid Ethereum address.
        
    Example:
        >>> price = await get_token_price("0x...")
        >>> print(f"Price: ${price}")
    """
    pass
```

### Type Hints

Always use type hints for function signatures:

```python
from typing import Optional

async def scan_wallet(
    wallet: TrackedWallet, 
    db: AsyncSession, 
    eth_price: float
) -> list[WhaleAlert]:
    """Scan a wallet for new large transactions."""
    pass
```

### Async/Await

- Use `async/await` for all I/O operations
- Prefer `httpx` over `requests` for async HTTP
- Use `AsyncSession` for database operations

---

## 📝 Commit Guidelines

### Commit Message Format

```
<type>(<scope>): <subject>

[optional body]

[optional footer]
```

### Types

- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Changes that do not affect the meaning of the code
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `perf`: A code change that improves performance
- `test`: Adding missing tests or correcting existing tests
- `chore`: Changes to the build process or auxiliary tools

### Examples

```
feat(api): add WebSocket support for real-time alerts

fix(whale-tracker): resolve duplicate alert issue for ETH transfers

docs(readme): update installation instructions for Windows

refactor(discord): extract embed formatting to separate module
```

---

## 🔄 Pull Request Process

1. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Follow coding standards
   - Add/update tests
   - Update documentation

3. **Run tests and linting**
   ```bash
   pytest tests/ -v
   ruff check .
   black --check .
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat: your feature description"
   ```

5. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request**
   - Go to the original repository on GitHub
   - Click "Compare & pull request"
   - Fill in the PR template
   - Link any related issues

### PR Checklist

- [ ] Code follows the project's coding standards
- [ ] All new and existing tests pass
- [ ] Documentation has been updated
- [ ] Commit messages follow the commit guidelines
- [ ] PR description clearly describes the changes
- [ ] PR is linked to relevant issues

### Review Process

1. A maintainer will review your PR
2. Address any requested changes
3. Once approved, a maintainer will merge your PR
4. Your contribution will be credited in the changelog

---

## 🧪 Testing Guidelines

### Writing Tests

- Place tests in the `tests/` directory
- Use descriptive test names
- Follow the AAA pattern: Arrange, Act, Assert
- Mock external services (APIs, blockchain)

### Test Structure

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_scan_wallet_detects_large_transfer():
    # Arrange
    wallet = TrackedWallet(address="0x...")
    db = AsyncMock()
    
    # Act
    alerts = await scan_wallet(wallet, db, eth_price=2000.0)
    
    # Assert
    assert len(alerts) > 0
    assert alerts[0].amount_usd >= settings.whale_threshold_usd
```

---

## 📚 Documentation

### What to Document

- New API endpoints
- New configuration options
- Breaking changes
- Migration guides
- Architecture decisions

### Documentation Files

- `README.md` - Project overview and quick start
- `docs/` - Detailed documentation
- Code comments and docstrings
- Type hints

---

## 🙏 Recognition

Contributors are recognized in:

- The `CHANGELOG.md` file
- Release notes
- The contributors section of the README

Thank you for contributing! 🎉

---

*This contributing guide is inspired by [Open Source Guides](https://opensource.guide/).*