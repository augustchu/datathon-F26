"""Fit the legacy GPU notebooks' capacity models and collect fit metrics."""

import numpy as np
import torch
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


def fit_capacity_histogram(preprocessor, train_features, train_beds, *, seed):
    """Bundle the already fitted preprocessing with a new baseline in bed units."""
    regressor = TransformedTargetRegressor(
        regressor=HistGradientBoostingRegressor(random_state=seed),
        func=np.log1p,
        inverse_func=np.expm1,
    ).fit(train_features, train_beds)
    return Pipeline([
        ("preprocessing", preprocessor),
        ("float32", FunctionTransformer(np.asarray, kw_args={"dtype": "float32"})),
        ("histogram_boosting", regressor),
    ])


def fit_capacity_mlp(train_features, train_beds, calibration_features, calibration_beds,
                     *, seed, max_epochs=300, patience=30):
    """Fit the original 128–64 network with Huber loss on log(1 + beds)."""
    if max_epochs < 1 or patience < 1:
        raise ValueError("max_epochs and patience must be positive.")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device_name = torch.cuda.get_device_name(device) if device.type == "cuda" else "CPU"
    print(f"Training on {device_name}." if device.type == "cuda" else "CUDA is unavailable. Training on CPU.")

    def tensors(features, beds):
        return (
            torch.as_tensor(features, dtype=torch.float32, device=device),
            torch.as_tensor(np.log1p(np.asarray(beds, dtype=float)), dtype=torch.float32, device=device)[:, None],
        )

    train_inputs, train_target = tensors(train_features, train_beds)
    calibration_inputs, calibration_target = tensors(calibration_features, calibration_beds)
    torch.manual_seed(seed)
    batch_size, learning_rate, weight_decay, dropout = 256, 1e-3, 1e-4, 0.2
    scheduler_factor, scheduler_patience, min_improvement = 0.5, 10, 1e-4
    loader = DataLoader(
        TensorDataset(train_inputs, train_target), batch_size=batch_size, shuffle=True,
        generator=torch.Generator().manual_seed(seed),
    )
    network = nn.Sequential(
        nn.Linear(train_inputs.shape[1], 128), nn.ReLU(), nn.Dropout(dropout),
        nn.Linear(128, 64), nn.ReLU(), nn.Dropout(dropout), nn.Linear(64, 1),
    ).to(device)
    optimizer = torch.optim.Adam(network.parameters(), lr=learning_rate, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, factor=scheduler_factor, patience=scheduler_patience,
    )
    huber = nn.HuberLoss(delta=1.0)
    best_loss, best_state, selected_epoch, bad_epochs = float("inf"), None, 0, 0
    history = []
    for epoch in range(1, max_epochs + 1):
        network.train()
        train_loss = torch.zeros((), device=device)
        epoch_learning_rate = optimizer.param_groups[0]["lr"]
        for inputs, target in loader:
            optimizer.zero_grad()
            loss = huber(network(inputs), target)
            loss.backward()
            optimizer.step()
            train_loss += loss.detach() * len(target)

        network.eval()
        with torch.no_grad():
            calibration_loss = huber(network(calibration_inputs), calibration_target).item()
        history.append({
            "epoch": epoch,
            "train_huber_log_beds": train_loss.item() / len(train_target),
            "calibration_huber_log_beds": calibration_loss,
            "learning_rate": epoch_learning_rate,
        })
        scheduler.step(calibration_loss)
        if calibration_loss < best_loss - min_improvement:
            best_loss = calibration_loss
            best_state = {key: value.detach().clone() for key, value in network.state_dict().items()}
            selected_epoch, bad_epochs = epoch, 0
        else:
            bad_epochs += 1
            if bad_epochs >= patience:
                break
    if best_state is None:
        raise ValueError("Training produced no finite calibration Huber loss.")
    network.load_state_dict(best_state)
    network.eval()
    print(f"Selected epoch {selected_epoch} of {len(history)} | calibration Huber loss {best_loss:.4f}")
    return {
        "model": network,
        "device": device,
        "history": history,
        "config": {
            "training_seed": seed, "device": device.type, "device_name": device_name,
            "max_epochs": max_epochs, "epochs_trained": len(history), "selected_epoch": selected_epoch,
            "patience": patience, "min_calibration_improvement": min_improvement,
            "selected_calibration_huber_log_beds": best_loss, "huber_delta": huber.delta,
            "hidden_units": [128, 64], "dropout": dropout, "batch_size": batch_size,
            "optimizer": "Adam", "learning_rate": learning_rate, "weight_decay": weight_decay,
            "lr_scheduler": "ReduceLROnPlateau", "scheduler_factor": scheduler_factor,
            "scheduler_patience": scheduler_patience,
        },
    }
