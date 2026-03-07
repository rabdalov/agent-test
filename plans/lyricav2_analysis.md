# LyricaV2 Integration Analysis

## Current Implementation

The current lyrics service uses the original Lyrica library (version 1.x) with the following code in `_search_lyrica` method:

```python
def _sync_search() -> str | None:
    try:
        from lyrica import Song  # type: ignore[import-untyped]
        song = Song(artist, title)
        lyrics = song.lyrics
        if lyrics and len(lyrics.strip()) > 50:
            return lyrics.strip()
        return None
    except Exception as e:
        logger.warning(f"Lyrica search failed for '{artist} - {title}': {e}")
        return None
```

## Expected Changes for LyricaV2

Based on typical library version upgrades, LyricaV2 likely includes:

### 1. Different Import Structure
- Old: `from lyrica import Song`
- Likely New: `from lyricav2 import Song` or `from lyrica import Song as LyricaV2`

### 2. Updated API Methods
- The constructor might have changed parameters
- The lyrics property might have a different name or return type
- Error handling might be different

### 3. Installation Requirements
- Need to update dependencies to include `lyricav2` instead of or alongside `lyrica`
- May require different installation command

### 4. Potential Async Support
- Version 2 might introduce async/await patterns
- Could improve performance compared to the current sync execution in executor

## Required Changes to Implement

### 1. Update Dependencies
Add LyricaV2 to the project dependencies in pyproject.toml

### 2. Update Import Statement
Change the import in the `_search_lyrica` method to use the new library

### 3. Update Method Implementation
Adapt to the new API of LyricaV2 while maintaining the same interface

### 4. Error Handling
Update error handling to match the new library's exception types

## Implementation Strategy

Since I don't have access to the exact LyricaV2 API, I'll implement a robust approach that handles both versions gracefully:

1. Try importing LyricaV2 first
2. Fall back to original Lyrica if V2 is not available
3. Maintain the same return interface

## Todo Items

- [ ] Research exact LyricaV2 API specification
- [ ] Update pyproject.toml with LyricaV2 dependency
- [ ] Modify _search_lyrica method to use LyricaV2
- [ ] Test the new implementation
- [ ] Update documentation if needed