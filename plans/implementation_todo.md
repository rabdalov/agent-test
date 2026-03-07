# LyricaV2 Implementation Todo List

## Pre-Implementation Research
- [x] Analyze current lyrics_service.py implementation
- [x] Understand current Lyrica (v1) usage
- [x] Research LyricaV2 library differences
- [x] Document expected changes

## Implementation Tasks

### 1. Update Project Dependencies
- [ ] Research the correct package name for LyricaV2
- [ ] Update pyproject.toml to include LyricaV2 dependency
- [ ] Remove old lyrica dependency if needed

### 2. Update Lyrics Service Implementation
- [ ] Modify the import statement in _search_lyrica method
- [ ] Update the API usage to match LyricaV2
- [ ] Test that the new implementation works correctly
- [ ] Ensure error handling still functions properly

### 3. Testing & Validation
- [ ] Test lyrics search functionality with LyricaV2
- [ ] Verify configuration flag (LYRICS_ENABLE_LYRICA) still works
- [ ] Confirm error cases are handled properly
- [ ] Validate that minimum 50-character check still works

### 4. Documentation Updates
- [ ] Update any relevant documentation
- [ ] Add notes about the LyricaV2 upgrade

## Implementation Order
1. Research package name for LyricaV2
2. Update dependencies
3. Update lyrics service code
4. Test functionality
5. Update documentation