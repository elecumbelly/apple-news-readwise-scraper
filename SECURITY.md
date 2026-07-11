# Security

## Secrets

This project keeps **no API keys or tokens in the repository**. Secrets are
read at runtime from the macOS Keychain, environment variables, or a local
gitignored `.env` file.

In Terminal, enter the token without writing it into your shell history:

```bash
read -s "READWISE_TOKEN?Paste your Readwise token, then press Return: "
security add-generic-password -U -s readwise-token -a "$USER" -w "$READWISE_TOKEN"
unset READWISE_TOKEN
```

## Permissions

- **Full Disk Access** for Terminal is needed to read Apple News data.
- **Accessibility / Automation** is used only for the fallback that copies
  article text from the News window.

## Reporting

Please report security concerns privately to the repository owner rather than
opening a public issue with sensitive details.
