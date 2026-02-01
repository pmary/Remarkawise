# Testing Guide for Remarkawise

This document covers both automated testing and manual real-life testing procedures.

## Table of Contents

1. [Automated Tests](#automated-tests)
2. [Manual Testing Procedure](#manual-testing-procedure)
3. [Test Data Setup](#test-data-setup)
4. [Troubleshooting](#troubleshooting)

---

## Automated Tests

### Running Tests

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage report
pytest --cov=remarkawise --cov-report=html

# Run specific test file
pytest tests/test_remarkable_client.py

# Run specific test class
pytest tests/test_remarkable_client.py::TestRemarkableClientAuth

# Run specific test
pytest tests/test_remarkable_client.py::TestRemarkableClientAuth::test_register_device_success

# Run with verbose output
pytest -v

# Run and show print statements
pytest -s
```

### Test Structure

```
tests/
├── conftest.py              # Fixtures and mock data
├── test_models.py           # Data model tests
├── test_remarkable_client.py # reMarkable API tests (mocked)
├── test_readwise_client.py  # Readwise API tests (mocked)
├── test_pdf_extractor.py    # PDF processing tests
├── test_state_manager.py    # State persistence tests
├── test_sync_engine.py      # Sync orchestration tests
└── test_cli.py              # CLI command tests
```

### Mock Strategy

Tests use `respx` for mocking HTTP requests to external APIs:
- reMarkable Cloud API responses are mocked
- Readwise API responses are mocked
- No real API calls are made during automated tests

---

## Manual Testing Procedure

### Prerequisites

Before manual testing, ensure you have:

1. **reMarkable Paper Pro device** with cloud sync enabled
2. **Readwise account** (free or paid)
3. **PDF documents with highlights** on your reMarkable
4. **Python 3.10+** installed

### Step 1: Environment Setup

```bash
# Clone and install
git clone https://github.com/pmary/Remarkawise.git
cd Remarkawise
pip install -e ".[dev]"

# Create environment file
cp .env.example .env
```

### Step 2: Get API Credentials

#### reMarkable Device Token

1. Run the auth command:
   ```bash
   remarkawise auth remarkable
   ```

2. Open https://my.remarkable.com/device/desktop/connect in your browser

3. Copy the 8-character code displayed on the website

4. Enter the code when prompted by the CLI

5. Copy the device token from the output and add it to `.env`:
   ```
   REMARKABLE_DEVICE_TOKEN=<your-token-here>
   ```

#### Readwise Access Token

1. Go to https://readwise.io/access_token

2. Copy your access token

3. Add it to `.env`:
   ```
   READWISE_ACCESS_TOKEN=<your-token-here>
   ```

### Step 3: Verify Configuration

```bash
# Verify Readwise token is valid
remarkawise auth readwise
# Expected: "Readwise token is valid!"

# List documents from reMarkable cloud
remarkawise list-documents
# Expected: Table showing your PDF documents
```

### Step 4: Prepare Test Document

On your reMarkable device:

1. **Upload a test PDF** (or use an existing one)
   - Recommended: Use a PDF with selectable text (not scanned images)
   - Document should be synced to reMarkable Cloud

2. **Add highlights**:
   - Open the PDF on your reMarkable
   - Select the highlighter tool
   - Highlight several text passages on different pages
   - Make sure to highlight at least:
     - A single line
     - Multiple consecutive lines
     - Text on different pages

3. **Sync to cloud**:
   - Ensure your reMarkable is connected to WiFi
   - Wait for the cloud sync icon to complete

### Step 5: Run Sync

```bash
# Check status before sync
remarkawise status
# Expected: Shows "No documents synced yet" or previous sync stats

# Run the sync
remarkawise sync
# Expected output:
# Starting sync...
# Syncing highlights...
# Sync completed successfully!
#   Documents processed: X
#   Highlights synced: Y

# Check status after sync
remarkawise status
# Expected: Shows updated stats with your document
```

### Step 6: Verify in Readwise

1. Go to https://readwise.io/dashboard

2. Look for your document in the "Books" section

3. Click on the document to view highlights

4. Verify:
   - [ ] Document title matches reMarkable document name
   - [ ] All highlighted text passages appear
   - [ ] Page numbers are correct
   - [ ] No duplicate highlights
   - [ ] Text is correctly extracted (no garbled text)

### Step 7: Test Incremental Sync

1. **Add new highlights** on your reMarkable:
   - Open the same PDF
   - Add 2-3 new highlights on different pages
   - Sync to cloud

2. **Run sync again**:
   ```bash
   remarkawise sync
   ```

3. **Verify**:
   - [ ] Only new highlights were synced (check count)
   - [ ] Previously synced highlights were not duplicated
   - [ ] New highlights appear in Readwise

### Step 8: Test Force Re-sync

```bash
# Force re-sync all documents
remarkawise sync --force
```

Verify:
- [ ] All highlights are re-processed
- [ ] No duplicates created in Readwise

### Step 9: Test Specific Document Sync

```bash
# List documents to get document ID
remarkawise list-documents

# Sync specific document (use ID from listing)
remarkawise sync --document <document-id>
```

---

## Test Cases Checklist

### Basic Functionality

- [ ] Authentication with reMarkable Cloud works
- [ ] Authentication with Readwise works
- [ ] List documents shows all PDF files
- [ ] Sync processes documents correctly
- [ ] Highlights are extracted accurately
- [ ] Highlights appear in Readwise

### Edge Cases

- [ ] PDF with no highlights - should process but sync 0 highlights
- [ ] PDF with native highlights (from other readers) - should be included
- [ ] PDF with many highlights (50+) - should handle without timeout
- [ ] Long highlight text (500+ characters) - should not truncate
- [ ] Highlight spanning multiple lines - should extract complete text
- [ ] Document in subfolder - should sync correctly
- [ ] Document with special characters in name - should handle correctly
- [ ] Document updated after initial sync - should sync new highlights only

### Error Handling

- [ ] Invalid reMarkable token - shows clear error message
- [ ] Invalid Readwise token - shows clear error message
- [ ] Network disconnection during sync - handles gracefully
- [ ] Corrupted PDF file - skips and continues with others
- [ ] Rate limited by Readwise - retries automatically

### Performance

- [ ] Sync 10+ documents - completes in reasonable time
- [ ] Sync 100+ highlights - no memory issues
- [ ] Large PDF (100+ pages) - processes without timeout

---

## Test Data Setup

### Creating Test PDFs

For consistent testing, create PDFs with known content:

```python
# Script to create test PDF
import fitz

doc = fitz.open()
for i in range(10):
    page = doc.new_page()
    text = f"""
    Page {i + 1}

    This is paragraph one with some text to highlight.

    This is paragraph two with different content.

    Key concept: Important information here.

    Another paragraph with more details about the topic.
    """
    page.insert_text((72, 72), text, fontsize=12)

doc.save("test_document.pdf")
doc.close()
```

### Expected Test Data

For manual testing, prepare:

1. **test_simple.pdf**: 5 pages, simple text, 3 highlights
2. **test_complex.pdf**: 20 pages, mixed content, 15 highlights
3. **test_existing_highlights.pdf**: PDF with native highlight annotations

---

## Troubleshooting

### Common Issues

#### "No highlights found in document"

- Ensure you used the highlighter tool (not pen or marker)
- Check that the PDF has selectable text (not scanned image)
- Verify document is synced to reMarkable Cloud

#### "Authentication failed"

- Device token may have expired; re-run `remarkawise auth remarkable`
- Readwise token may be invalid; get new one from readwise.io/access_token

#### "Failed to extract text"

- PDF may be image-based (OCR not supported yet)
- Highlight region may not contain text
- PDF encoding may be incompatible

#### Highlights appear garbled or incomplete

- PDF text encoding issue
- Try a different PDF to verify

### Debug Mode

For detailed debugging:

```bash
# Set log level to debug
export REMARKAWISE_LOG_LEVEL=DEBUG
remarkawise sync
```

### Checking Sync State

```bash
# View the SQLite database directly
sqlite3 ~/.remarkawise/sync_state.db

# List synced documents
SELECT * FROM sync_state;

# List synced highlights
SELECT * FROM synced_highlights;
```

### Resetting Sync State

To start fresh:

```bash
# Remove all sync data
rm -rf ~/.remarkawise

# Re-run sync
remarkawise sync
```

---

## Reporting Issues

When reporting bugs, please include:

1. **Command that failed**: Full command and arguments
2. **Error output**: Complete error message
3. **Environment**:
   - OS and version
   - Python version (`python --version`)
   - Remarkawise version (`remarkawise --version`)
4. **PDF details** (if relevant):
   - Source of PDF (how it was created)
   - Number of pages
   - Whether it has selectable text
5. **Steps to reproduce**: Detailed steps to recreate the issue
