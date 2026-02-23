# Python API

```python
from pipespec_validator import validate_file

result = validate_file("pipeline.pipespec.json", semantic_checks=True)
if not result.ok:
    for err in result.errors:
        print(err.instance_path, err.message)