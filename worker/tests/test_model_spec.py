import numpy as np
import pytest
import yaml

from worker.app.model_spec import load_all_specs, load_model_spec
from worker.tests.conftest import write_model_yaml


def test_reads_classes_thresholds_and_iou(tmp_path):
    spec = load_model_spec(write_model_yaml(tmp_path / "m", "m", {"a": 0.11, "b": 0.72}, nms_iou=0.55))
    assert spec.name == "m"
    assert spec.classes == ("a", "b")
    assert spec.nc == 2
    np.testing.assert_allclose(spec.thresholds, [0.11, 0.72])
    assert spec.nms_iou == 0.55
    assert spec.input_size == 640
    assert spec.threshold("b") == pytest.approx(0.72)
    assert spec.class_id("b") == 1
    assert spec.onnx_path == tmp_path / "m" / "best.onnx"


def test_thresholds_follow_class_order_not_dict_order(tmp_path):
    folder = write_model_yaml(tmp_path / "m", "m", {"a": 0.1, "b": 0.2, "c": 0.3})
    raw = yaml.safe_load((folder / "model.yaml").read_text(encoding="utf-8"))
    raw["thresholds"] = {"c": 0.3, "a": 0.1, "b": 0.2}
    (folder / "model.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    np.testing.assert_allclose(load_model_spec(folder).thresholds, [0.1, 0.2, 0.3])


def test_unknown_class_is_an_error(toy_spec):
    with pytest.raises(KeyError, match="нет класса"):
        toy_spec.class_id("helmet")


def test_missing_threshold_is_an_error(tmp_path):
    folder = write_model_yaml(tmp_path / "m", "m", {"a": 0.1, "b": 0.2})
    raw = yaml.safe_load((folder / "model.yaml").read_text(encoding="utf-8"))
    del raw["thresholds"]["b"]
    (folder / "model.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="нет порогов"):
        load_model_spec(folder)


def test_output_shape_must_match_class_count(tmp_path):
    folder = write_model_yaml(tmp_path / "m", "m", {"a": 0.1, "b": 0.2})
    raw = yaml.safe_load((folder / "model.yaml").read_text(encoding="utf-8"))
    raw["output"]["shape"] = [1, 11, 8400]
    (folder / "model.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="не согласуется"):
        load_model_spec(folder)


def test_missing_file_points_to_sync_script(tmp_path):
    with pytest.raises(FileNotFoundError, match="sync_models"):
        load_model_spec(tmp_path / "nope")


def test_load_all_specs(tmp_path):
    write_model_yaml(tmp_path / "one", "one", {"a": 0.1})
    write_model_yaml(tmp_path / "two", "two", {"b": 0.2, "c": 0.3})
    (tmp_path / "junk").mkdir()
    assert set(load_all_specs(tmp_path)) == {"one", "two"}


@pytest.mark.models
def test_real_model_yaml_is_readable(real_specs):
    assert real_specs["ppe"].classes == ("person", "helmet", "no_helmet", "vest", "mask", "no_mask", "vehicle")
    assert real_specs["hazard"].classes == ("fire", "smoke", "cigarette")
    for spec in real_specs.values():
        assert ((spec.thresholds > 0) & (spec.thresholds < 1)).all()
