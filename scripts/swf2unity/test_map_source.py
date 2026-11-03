"""Regression checks for map extraction's geometry and literal boundaries."""
import struct

import pytest

import map_source as ms
import swfparse as sp


def test_inspector_binds_to_explicit_source_frame_range():
    text = '''function __setProp___id0__Pads(param1:int):* {
      if (param1 >= 10 && param1 <= 16) {
        this.__id0_.tCell = "Garden1";
        this.__id0_.tPad = "Bottom";
        this.__id0_.strAsk = "Go; now?";
      }
    }'''
    result = ms.inspectors(text)
    assert result[0]['start'] == 10 and result[0]['end'] == 16
    assert result[0]['instance'] == '__id0_'
    assert result[0]['properties'] == {'tCell': 'Garden1', 'tPad': 'Bottom', 'strAsk': 'Go; now?'}


def test_dynamic_exit_is_not_silently_replaced_with_a_literal():
    text = '''function __setProp_x(param1:int):* {
      if (param1 >= 1 && param1 <= 2) { this.__id0_.tCell = getDestination(); }
    }'''
    with pytest.raises(ValueError, match='Not an inspector literal'):
        ms.inspectors(text)


def test_guardless_inspector_is_rejected():
    with pytest.raises(ValueError, match='frame guard'):
        ms.inspectors('function __setProp_x():* { this.__id0_.tPad = "Spawn"; }')


def test_as_apostrophe_and_arrays_are_data_not_code():
    assert ms.literal('"Don\\\'t"') == "Don't"
    assert ms.literal('[1,2,3]') == [1, 2, 3]
    with pytest.raises(ValueError):
        ms.literal('[getDestination()]')


def test_vector_rectangle_preserves_negative_local_pivot():
    assert ms.straight_path('M11.05 10 L-11.05 10 -11.05 -10 11.05 -10 11.05 10 Z') == [
        [11.05, 10], [-11.05, 10], [-11.05, -10], [11.05, -10]]


@pytest.mark.parametrize('path', ['M0 0 Q1 2 3 4 Z', 'M0 0 L10 0 0 10 Z M2 2 L3 2 2 3 Z'])
def test_curves_and_holes_cannot_become_false_rectangle_colliders(path):
    with pytest.raises(ValueError):
        ms.straight_path(path)


def test_button_uses_hit_state_instead_of_visible_up_art():
    # Two distinct definitions: normal button art and its actual hit region.
    record = lambda flags, cid: bytes([flags]) + struct.pack('<HH', cid, 1) + sp.encode_matrix(ms.IDENT) + sp.encode_cxform_alpha(((256,)*4, (0,)*4))
    body = struct.pack('<HBH', 20, 0, 0) + record(1, 30) + record(8, 40) + b'\0'
    char = sp.Character(20, 'button', 34, raw=sp.tag(34, body))
    movie = sp.Movie(10, (0, 100, 0, 100), 24, 1, {20: char}, [], {}, [])
    assert [r['char'] for r in ms.button_hit(movie, 20)] == [40]


def test_blank_unity_render_cannot_pass_a_successful_physics_build(tmp_path):
    from PIL import Image
    from build_map_prototype import verify_renders
    (tmp_path / 'snapshots' / 'Enter').mkdir(parents=True)
    (tmp_path / 'unity_build').mkdir()
    Image.new('RGBA', (8, 8), (160, 100, 60, 255)).save(tmp_path / 'snapshots' / 'Enter' / 'snapshot.png')
    Image.new('RGBA', (8, 8), (0, 0, 0, 255)).save(tmp_path / 'unity_build' / 'Enter-unity.png')
    with pytest.raises(ValueError, match='render comparison failed'):
        verify_renders(tmp_path, ['Enter'])


def test_render_comparison_uses_the_same_background_for_source_alpha(tmp_path):
    from PIL import Image
    from build_map_prototype import verify_renders
    (tmp_path / 'snapshots' / 'Enter').mkdir(parents=True)
    (tmp_path / 'unity_build').mkdir()
    rgba = Image.new('RGBA', (8, 8), (160, 100, 60, 128))
    rgba.save(tmp_path / 'snapshots' / 'Enter' / 'snapshot.png')
    Image.alpha_composite(Image.new('RGBA', (8, 8), (0, 0, 0, 255)), rgba).save(tmp_path / 'unity_build' / 'Enter-unity.png')
    assert verify_renders(tmp_path, ['Enter'])['status'] == 'passed'
