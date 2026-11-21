# Capacity models and Weights & Biases

Open the model notebooks from the Databricks Git folder so their shared files are available. Run the dependency cell before training. The [model evaluation](MODEL_EVALUATION.md) records the comparison on 522 facilities and why the facilities-only neural network is the next candidate to validate.

The [population density notebooks](models%20with%20population%20density/) compare three MLPs on matching split and holdout fingerprints. Run the facilities-only baseline, then the density and density-plus-NFHS models. The [data handoff](models%20with%20population%20density/CLAUDE_HANDOFF.md) explains district and PIN formats and Databricks configuration.

## Experiment tracking

The model-export cells use the project in `WANDB_PROJECT` or default to `datathon-f26`. Runs default to offline. For a shared project, set `WANDB_MODE=online`, `WANDB_ENTITY` and `WANDB_PROJECT` before running. Provide `WANDB_API_KEY` through the notebook environment or a Databricks secret.

Each run records split sizes, configuration and aggregate scores in beds. The output is a W&B model artifact. A neural-network artifact pairs the training-fitted preprocessor with the exact network weights used for evaluation. A scikit-learn artifact contains the fitted pipeline. Both include a JSON manifest and aggregate metrics.

Use a pinned `entity/project/artifact:vN` version when downloading a bundle. `wandb.Api().artifact(...).download()` fetches its files and `artifact.verify(...)` checks them against the manifest. For inference, follow the input preparation and target transformation recorded for that model. [W&B artifact guide](https://docs.coreweave.com/products/wandb/artifacts/download-and-use-an-artifact)

When loading the shared trainer's `preprocessor.skops` from a trusted run, review the types reported by `skops.io.get_untrusted_types`. Fitted scikit-learn imputers can include NumPy's `numpy.dtype` metadata. Verify that reloaded preprocessing reproduces the fitted transform before use.

An offline run stays on the machine where it was logged. Preserve the W&B run directory and artifact staging directory if you will sync it later. [W&B environment variables](https://docs.coreweave.com/products/wandb/track/environment-variables)
