# Security Policy

## 🔐 Supported Versions

We release patches for security vulnerabilities. Currently supported versions:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | ✅ Active support  |
| < 1.0   | ❌ Not supported   |

## 🛡️ Security Features

Smart Money Tracker implements several security best practices:

### Secrets Management
- All sensitive credentials are loaded from environment variables
- `.env` files are excluded from version control via `.gitignore`
- No hardcoded secrets in source code

### API Security
- Input validation on all endpoints via Pydantic
- SQL injection protection via SQLAlchemy ORM
- Request size limits enforced by FastAPI

### Bot Security
- Token-based authentication for Discord and Telegram
- Minimal required permissions for bots
- No direct database access from bot layer

### Docker Security
- Non-root user in production container
- Multi-stage build to minimize attack surface
- Health checks for container monitoring

## 🚨 Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security vulnerability, please report it responsibly.

### How to Report

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via:

1. **GitHub Security Advisories** (Preferred)
   - Go to the [Security Advisories](https://github.com/yourusername/smart-money-tracker/security/advisories) page
   - Click "Report a vulnerability"
   - Fill in the details

2. **Email** (Alternative)
   - Send an email to: security@example.com
   - Subject: `[SECURITY] Smart Money Tracker Vulnerability`

### What to Include

Please include the following information:

- **Type of vulnerability** (e.g., injection, authentication bypass, etc.)
- **Full paths of source file(s) related to the issue**
- **Step-by-step instructions to reproduce the issue**
- **Proof-of-concept or exploit code** (if possible)
- **Impact of the vulnerability**
- **Any possible mitigations**

### Response Timeline

| Time | Action |
|------|--------|
| 24 hours | Acknowledge receipt of report |
| 72 hours | Initial assessment and classification |
| 7 days | Detailed response with remediation plan |
| 14 days | Patch development and testing |
| 30 days | Public disclosure (if applicable) |

### Disclosure Policy

- We follow **Coordinated Vulnerability Disclosure (CVD)**
- We ask that you give us reasonable time to fix the issue before public disclosure
- We will credit you in the security advisory (unless you prefer to remain anonymous)

## 🔧 Security Best Practices for Users

### Environment Setup

1. **Never commit `.env` files**
   ```bash
   # Ensure .env is in .gitignore
   echo ".env" >> .gitignore
   ```

2. **Use strong, unique API keys**
   - Generate new keys for each deployment
   - Rotate keys periodically
   - Restrict key permissions where possible

3. **Secure your bot tokens**
   - Keep Discord/Telegram tokens private
   - Regenerate tokens if compromised
   - Use separate tokens for development/production

### Production Deployment

1. **Enable HTTPS**
   ```nginx
   server {
       listen 443 ssl;
       ssl_certificate /path/to/cert.pem;
       ssl_certificate_key /path/to/key.pem;
   }
   ```

2. **Restrict CORS**
   ```python
   # In production, restrict origins
   app.add_middleware(
       CORSMiddleware,
       allow_origins=["https://yourdomain.com"],
       allow_methods=["GET", "POST", "DELETE"],
   )
   ```

3. **Add rate limiting**
   ```python
   # Consider using slowapi or similar
   from slowapi import Limiter
   limiter = Limiter(key_func=get_remote_address)
   ```

4. **Use a reverse proxy**
   - Nginx or Caddy in front of the API
   - Configure request limits and timeouts
   - Enable access logging

5. **Regular updates**
   ```bash
   # Keep dependencies updated
   pip install --upgrade -r requirements.txt
   ```

### Docker Security

1. **Run as non-root user** (already configured)
2. **Use secrets management**
   ```yaml
   # docker-compose.yml
   secrets:
     discord_token:
       file: ./secrets/discord_token.txt
   ```

3. **Limit container resources**
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '1'
         memory: 512M
   ```

## 🔍 Security Checklist

Before deploying to production:

- [ ] All secrets are in environment variables
- [ ] `.env` file is not committed to Git
- [ ] CORS is restricted to your domain
- [ ] HTTPS is enabled
- [ ] Rate limiting is configured
- [ ] Bot has minimal required permissions
- [ ] Dependencies are up to date
- [ ] Container runs as non-root user
- [ ] Access logs are enabled
- [ ] Regular backups are configured

## 📚 Additional Resources

- [OWASP API Security Top 10](https://owasp.org/www-project-api-security/)
- [Discord Bot Security Best Practices](https://discord.com/developers/docs/topics/security)
- [Telegram Bot Security Guidelines](https://core.telegram.org/bots#security)

---

*Thank you for helping keep Smart Money Tracker secure! 🔒*