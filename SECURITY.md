# Security

## Secrets

This project keeps **no API keys or tokens in the repository**. Secrets are
read at runtime from the macOS Keychain, environment variables, or a local
gitignored `.env` file.

```bash
security add-generic-password -s readwise-token -a "$USER" -w YOUR_TOKEN
security add-generic-password -s imgbb-api-key -a "$USER" -w YOUR_KEY  # optional
```

## Permissions

- **Full Disk Access** for Terminal is needed to read Apple News data.
- **Accessibility / Automation** is used only for the fallback that copies
  article text from the News window.

## Reporting

Please report security concerns privately to the repository owner rather than
opening a public issue with sensitive details.
