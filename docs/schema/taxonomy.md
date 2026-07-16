# Component Taxonomy (v1)

PipeSpec v1 includes data engineering categories and ML lifecycle categories.

- Extractor
- Transformer
- Loader
- Reconciliator
- QualityCheck
- FeatureEngineering
- ModelTraining
- ModelEvaluation
- ModelInference
- Notifier
- Sensor
- Custom

These categories are intended to be inferable from natural language descriptions.
They are used downstream to select templates, infer defaults, and drive compilation.

## ML Artifact Kinds

`io_spec[].kind` supports the original data-oriented values:

- file
- table
- api
- object
- stream

It also supports ML-oriented values:

- features
- model
- metrics
- predictions
- embedding

Common ML formats include `pickle`, `pkl`, `joblib`, `onnx`, `pmml`, `mlflow`,
`skops`, `npy`, and `npz`. For example, a model-training component can emit:

```json
{
  "name": "risk_model",
  "direction": "output",
  "kind": "model",
  "format": "pickle",
  "path_pattern": "models/risk_model.pkl",
  "connection_id": "model_registry"
}
```
