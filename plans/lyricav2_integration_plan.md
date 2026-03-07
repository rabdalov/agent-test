# LyricaV2 Integration Plan

## Overview
This document outlines the plan to upgrade the lyrics service from the original Lyrica library to LyricaV2.

## Current State
- The lyrics service currently uses `lyrica` library version 1.x
- The implementation is in [app/lyrics_service.py](file:///x:/rabdalov/db_scripts/rabdalov/agent-test/agent-test/app/lyrics_service.py:89) in the `_search_lyrica` method
- Configuration flag `LYRICS_ENABLE_LYRICA` controls whether this provider is used
- The current implementation runs synchronously in an executor

## Target State
- Upgrade to use LyricaV2 library
- Maintain the same external interface and behavior
- Potentially improve performance if LyricaV2 supports async operations

## Detailed Changes Required

### 1. Dependency Management
**File:** `pyproject.toml`
- Replace or add `lyricav2` dependency instead of the old `lyrica`
- Need to research the correct package name for LyricaV2

### 2. Code Implementation
**File:** `app/lyrics_service.py`
- Update the import statement in the `_search_lyrica` method
- Adapt to the new API of LyricaV2
- Maintain the same error handling and validation logic

### 3. Specific Implementation Details

#### Current Implementation:
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

#### Expected Updated Implementation:
```python
def _sync_search() -> str | None:
    try:
        from lyricav2 import Song  # Updated import
        song = Song(artist, title)  # Check if constructor is the same
        lyrics = song.lyrics        # Check if property name is the same
        if lyrics and len(lyrics.strip()) > 50:
            return lyrics.strip()
        return None
    except Exception as e:
        logger.warning(f"LyricaV2 search failed for '{artist} - {title}': {e}")
        return None
```

## Implementation Steps

1. **Research LyricaV2 Package Name**
   - Determine the exact package name for installation
   - Check if it's `lyricav2`, `LyricaV2`, or something else

2. **Update Dependencies**
   - Modify `pyproject.toml` to include the LyricaV2 package

3. **Update Import Statement**
   - Change the import in `app/lyrics_service.py` from `lyrica` to `lyricav2`

4. **Test API Compatibility**
   - Verify that the Song class constructor and lyrics property work the same way
   - If different, adapt the implementation accordingly

5. **Testing**
   - Test that lyrics search still works correctly
   - Verify error handling still functions properly
   - Confirm that the configuration flag still controls the feature

## Risk Mitigation

- Keep the same external interface to avoid breaking changes elsewhere
- Maintain the same validation logic (minimum 50 characters)
- Preserve error handling and logging
- Consider fallback to original Lyrica if V2 has compatibility issues

## Success Criteria

- Lyrics search functionality continues to work when `LYRICS_ENABLE_LYRICA=true`
- No breaking changes to the LyricsService interface
- Proper error handling maintained
- Configuration continues to work as expected