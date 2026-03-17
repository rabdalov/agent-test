#!/usr/bin/env python3
"""Test script for iteration 43 - Refactoring VolumeSegment and universal merge_segments."""

import json
import sys
from pathlib import Path

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.chorus_detector import (
    ChorusDetector,
    SegmentScore,
    VolumeSegment,
    build_volume_segments,
    load_volume_segments,
    save_volume_segments,
    should_merge_same_type,
)


def test_segment_score():
    """Test the new SegmentScore class."""
    print("Testing SegmentScore class...")
    
    # Create a SegmentScore
    score = SegmentScore(
        id=1,
        vocal_energy=0.8,
        chroma_variance=0.3,
        sim_score=0.7,
        hpss_score=0.6,
        tempo_score=0.5
    )
    
    # Test to_dict
    score_dict = score.to_dict()
    expected = {
        "id": 1,
        "vocal_energy": 0.8,
        "chroma_variance": 0.3,
        "sim_score": 0.7,
        "hpss_score": 0.6,
        "tempo_score": 0.5,
    }
    assert score_dict == expected, f"Expected {expected}, got {score_dict}"
    
    # Test from_dict
    restored = SegmentScore.from_dict(expected)
    assert restored.id == 1
    assert restored.vocal_energy == 0.8
    assert restored.chroma_variance == 0.3
    assert restored.sim_score == 0.7
    assert restored.hpss_score == 0.6
    assert restored.tempo_score == 0.5
    
    print("PASS: SegmentScore tests passed")


def test_volume_segment():
    """Test the updated VolumeSegment class."""
    print("Testing VolumeSegment class...")
    
    # Create scores
    score1 = SegmentScore(id=1, vocal_energy=0.8, sim_score=0.7)
    score2 = SegmentScore(id=2, vocal_energy=0.6, sim_score=0.5)
    
    # Create VolumeSegment with scores list
    vs = VolumeSegment(
        start=0.0,
        end=10.0,
        volume=0.5,
        segment_type="verse",
        backend="test",
        scores=[score1, score2],
        id=1
    )
    
    # Test properties
    assert vs.duration == 10.0
    assert vs.subsegment_count == 2
    assert vs.get_first_id() == 1
    assert vs.get_last_id() == 2
    assert vs.get_id_range() == "#1-2"
    
    # Test to_dict
    vs_dict = vs.to_dict()
    expected = {
        "id": 1,
        "start": 0.0,
        "end": 10.0,
        "volume": 0.5,
        "segment_type": "verse",
        "backend": "test",
        "scores": [
            {"id": 1, "vocal_energy": 0.8, "chroma_variance": 0.0, "sim_score": 0.7, "hpss_score": 0.0, "tempo_score": 0.0},
            {"id": 2, "vocal_energy": 0.6, "chroma_variance": 0.0, "sim_score": 0.5, "hpss_score": 0.0, "tempo_score": 0.0}
        ]
    }
    assert vs_dict == expected, f"Expected {expected}, got {vs_dict}"
    
    # Test from_dict
    restored = VolumeSegment.from_dict(expected)
    assert restored.start == 0.0
    assert restored.end == 10.0
    assert restored.volume == 0.5
    assert restored.segment_type == "verse"
    assert restored.backend == "test"
    assert len(restored.scores) == 2
    assert restored.scores[0].id == 1
    assert restored.scores[1].id == 2
    assert restored.id == 1
    
    print("PASS: VolumeSegment tests passed")


def test_chorus_detector_init():
    """Test ChorusDetector with new parameters."""
    print("Testing ChorusDetector initialization...")
    
    detector = ChorusDetector(
        min_duration_sec=3.0,
        vocal_silence_threshold=0.1,
        boundary_merge_tolerance_sec=1.5,
        chorus_volume=0.6,
        default_volume=0.3,
    )
    
    assert detector._min_duration == 3.0
    assert detector._vocal_silence_threshold == 0.1
    assert detector._boundary_merge_tolerance == 1.5
    assert detector._chorus_volume == 0.6
    assert detector._default_volume == 0.3
    
    print("PASS: ChorusDetector initialization tests passed")


def test_should_merge_same_type():
    """Test the should_merge_same_type predicate."""
    print("Testing should_merge_same_type predicate...")
    
    seg1 = VolumeSegment(start=0.0, end=5.0, volume=0.5, segment_type="verse", id=1)
    seg2 = VolumeSegment(start=5.0, end=10.0, volume=0.5, segment_type="verse", id=2)
    seg3 = VolumeSegment(start=10.0, end=15.0, volume=0.4, segment_type="chorus", id=3)
    
    # Same type should return True
    assert should_merge_same_type(seg1, seg2) == True
    
    # Different types should return False
    assert should_merge_same_type(seg1, seg3) == False
    
    # With None types
    seg4 = VolumeSegment(start=15.0, end=20.0, volume=0.5, segment_type=None, id=4)
    seg5 = VolumeSegment(start=20.0, end=25.0, volume=0.5, segment_type=None, id=5)
    assert should_merge_same_type(seg4, seg5) == True
    
    print("PASS: should_merge_same_type tests passed")


def test_save_load_volume_segments():
    """Test saving and loading volume segments with new format."""
    print("Testing save/load volume segments...")
    
    # Create test segments
    score1 = SegmentScore(id=1, vocal_energy=0.8, sim_score=0.7)
    score2 = SegmentScore(id=2, vocal_energy=0.6, sim_score=0.5)
    
    segments = [
        VolumeSegment(
            start=0.0, end=10.0, volume=0.5, segment_type="verse", 
            backend="test", scores=[score1], id=1
        ),
        VolumeSegment(
            start=10.0, end=20.0, volume=0.4, segment_type="chorus", 
            backend="test", scores=[score2], id=2
        )
    ]
    
    # Create temp file
    temp_file = Path("temp_test_segments.json")
    
    try:
        # Save segments
        save_volume_segments(segments, temp_file)
        
        # Load segments
        loaded_segments = load_volume_segments(temp_file)
        
        # Verify
        assert len(loaded_segments) == 2
        assert loaded_segments[0].start == 0.0
        assert loaded_segments[0].end == 10.0
        assert loaded_segments[0].segment_type == "verse"
        assert len(loaded_segments[0].scores) == 1
        assert loaded_segments[0].scores[0].id == 1
        assert loaded_segments[0].scores[0].vocal_energy == 0.8
        
        assert loaded_segments[1].start == 10.0
        assert loaded_segments[1].end == 20.0
        assert loaded_segments[1].segment_type == "chorus"
        assert len(loaded_segments[1].scores) == 1
        assert loaded_segments[1].scores[0].id == 2
        assert loaded_segments[1].scores[0].vocal_energy == 0.6
        
        print("PASS: Save/load volume segments tests passed")
    finally:
        # Cleanup
        if temp_file.exists():
            temp_file.unlink()


def test_build_volume_segments():
    """Test build_volume_segments with new structure."""
    print("Testing build_volume_segments...")
    
    from app.chorus_detector import SegmentInfo
    
    # Create test segment infos
    segment_infos = [
        SegmentInfo(
            start=0.0, end=10.0, segment_type="verse", backend="test",
            scores={"vocal_energy": 0.8, "sim_score": 0.7, "hpss_score": 0.6}
        ),
        SegmentInfo(
            start=10.0, end=20.0, segment_type="chorus", backend="test",
            scores={"vocal_energy": 0.6, "sim_score": 0.8, "hpss_score": 0.7}
        )
    ]
    
    # Build volume segments
    segments = build_volume_segments(
        chorus_segments=[],
        audio_duration=30.0,
        chorus_volume=0.6,
        default_volume=0.3,
        segment_infos=segment_infos
    )
    
    # Verify
    assert len(segments) >= 2  # Should have at least the two segments plus possible fillers
    found_ver = False
    found_chorus = False
    
    for seg in segments:
        if seg.segment_type == "verse":
            found_ver = True
            assert len(seg.scores) == 1  # Should have one score object
            assert seg.scores[0].vocal_energy == 0.8
        elif seg.segment_type == "chorus":
            found_chorus = True
            assert len(seg.scores) == 1  # Should have one score object
            assert seg.scores[0].vocal_energy == 0.6
    
    assert found_ver, "Should have found a verse segment"
    assert found_chorus, "Should have found a chorus segment"
    
    print("PASS: build_volume_segments tests passed")


def test_merge_segments():
    """Test the new merge_segments functionality."""
    print("Testing merge_segments functionality...")
    
    detector = ChorusDetector(
        chorus_volume=0.6,
        default_volume=0.3,
    )
    
    # Create test segments
    score1 = SegmentScore(id=1, vocal_energy=0.8, sim_score=0.7)
    score2 = SegmentScore(id=2, vocal_energy=0.8, sim_score=0.7)  # Same type as 1
    score3 = SegmentScore(id=3, vocal_energy=0.6, sim_score=0.8)  # Different type
    
    segments = [
        VolumeSegment(
            start=0.0, end=5.0, volume=0.5, segment_type="verse", 
            backend="test", scores=[score1], id=1
        ),
        VolumeSegment(
            start=5.0, end=10.0, volume=0.5, segment_type="verse",  # Same type
            backend="test", scores=[score2], id=2
        ),
        VolumeSegment(
            start=10.0, end=15.0, volume=0.4, segment_type="chorus",  # Different type
            backend="test", scores=[score3], id=3
        )
    ]
    
    # Test merging by same type
    merged = detector.merge_segments(segments, should_merge_same_type)
    
    # Should have 2 segments: one merged verse, one chorus
    assert len(merged) == 2, f"Expected 2 segments, got {len(merged)}"
    
    # First segment should be merged verse (0.0 to 10.0)
    merged_ver = merged[0] if merged[0].segment_type == "verse" else merged[1]
    merged_chorus = merged[1] if merged[0].segment_type == "verse" else merged[0]
    
    assert merged_ver.segment_type == "verse"
    assert abs(merged_ver.start - 0.0) < 0.001
    assert abs(merged_ver.end - 10.0) < 0.001
    assert len(merged_ver.scores) == 2  # Should have both scores
    
    assert merged_chorus.segment_type == "chorus"
    assert abs(merged_chorus.start - 10.0) < 0.001
    assert abs(merged_chorus.end - 15.0) < 0.001
    assert len(merged_chorus.scores) == 1
    
    print("PASS: merge_segments functionality tests passed")


def main():
    """Run all tests."""
    print("Running tests for iteration 43...")
    
    test_segment_score()
    test_volume_segment()
    test_chorus_detector_init()
    test_should_merge_same_type()
    test_save_load_volume_segments()
    test_build_volume_segments()
    test_merge_segments()
    
    print("\nAll tests passed! Iteration 43 implementation is working correctly.")


if __name__ == "__main__":
    main()