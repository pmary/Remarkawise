# Remarkawise

Sync highlighted PDFs from your reMarkable Paper Pro to Readwise Reader.

## Features

- **Automatic highlight extraction**: Extracts highlights from reMarkable's annotation files (`.rm` format)
- **PDF text extraction**: Maps highlight regions to actual text content using PyMuPDF
- **Native PDF highlights**: Also syncs highlights embedded directly in PDFs
- **Incremental sync**: Only syncs new or modified documents and highlights
- **State tracking**: Remembers what has been synced to avoid duplicates

## Installation

```bash
# Clone the repository
git clone https://github.com/pmary/Remarkawise.git
cd Remarkawise

# Install with pip
pip install -e .

# Or with development dependencies
pip install -e ".[dev]"
```

## Configuration

### 1. Set up reMarkable Cloud access

```bash
remarkawise auth remarkable
```

This will guide you through the device registration process:
1. Visit https://my.remarkable.com/device/desktop/connect
2. Enter the one-time code shown on the website
3. Save the device token to your `.env` file

### 2. Set up Readwise access

1. Get your Readwise access token from https://readwise.io/access_token
2. Add it to your `.env` file:

```bash
cp .env.example .env
# Edit .env and add your tokens
```

Your `.env` file should look like:

```
REMARKABLE_DEVICE_TOKEN=your_device_token_here
READWISE_ACCESS_TOKEN=your_readwise_token_here
```

### 3. Verify configuration

```bash
remarkawise auth readwise
```

## Usage

### Sync highlights

```bash
# Sync all new highlights
remarkawise sync

# Force re-sync everything
remarkawise sync --force

# Sync a specific document
remarkawise sync --document <document-id>
```

### View status

```bash
remarkawise status
```

### List documents

```bash
remarkawise list-documents
```

## How it works

1. **Fetch documents**: Connects to reMarkable Cloud and lists all PDF documents
2. **Download & extract**: Downloads document archives containing PDFs and annotation files
3. **Parse highlights**: Reads `.rm` files to find highlighter strokes and their coordinates
4. **Extract text**: Uses PyMuPDF to extract the actual text content from highlight regions
5. **Upload to Readwise**: Sends highlights to Readwise API with document metadata
6. **Track state**: Stores sync state locally to enable incremental updates

## Architecture

```
src/remarkawise/
├── cli.py              # Command-line interface
├── config.py           # Configuration management
├── models.py           # Data models (Pydantic)
├── remarkable/
│   ├── client.py       # reMarkable Cloud API client
│   └── parser.py       # .rm file parser for highlights
├── readwise/
│   └── client.py       # Readwise API client
├── pdf/
│   └── extractor.py    # PDF text extraction
└── sync/
    ├── engine.py       # Sync orchestration
    └── state.py        # State persistence (SQLite)
```

## Limitations

- Currently supports PDF documents only (not EPUB or notebooks)
- Requires reMarkable Cloud sync to be enabled
- Handwritten annotations are not converted to text (only highlighter marks)

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run linting
ruff check src/

# Run type checking
mypy src/

# Run tests
pytest
```

## License

MIT License - see LICENSE file for details.

## Credits

- [reMarkable](https://remarkable.com/) for the Paper Pro device
- [Readwise](https://readwise.io/) for the highlight management platform
- [PyMuPDF](https://pymupdf.readthedocs.io/) for PDF processing
