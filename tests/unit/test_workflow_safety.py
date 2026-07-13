from pathlib import Path


WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"


def test_direct_dev_pushes_run_ci():
    workflow = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")

    assert 'branches: [ "main", "dev" ]' in workflow


def test_production_image_is_only_published_from_main():
    workflow = (WORKFLOWS / "build-image.yml").read_text(encoding="utf-8")

    push_section = workflow.split("push:", maxsplit=1)[1].split(
        "env:", maxsplit=1
    )[0]
    assert 'branches: [ "main" ]' in push_section
