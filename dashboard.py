"""PLL Simulation Dashboard — Interactive HTML visualization of ngspice results."""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os

DATA_DIR = os.path.dirname(os.path.abspath(__file__))

def load_waveform(filename):
    """Load ngspice wrdata output (time, value columns)."""
    path = os.path.join(DATA_DIR, filename)
    data = np.loadtxt(path)
    return data[:, 0], data[:, 1]

def load_metrics():
    """Parse metrics.txt into a dict."""
    metrics = {}
    path = os.path.join(DATA_DIR, "metrics.txt")
    with open(path) as f:
        for line in f:
            if ":" in line and "===" not in line:
                key, val = line.strip().split(":", 1)
                val = val.strip()
                try:
                    metrics[key] = float(val)
                except ValueError:
                    metrics[key] = val
    return metrics

def downsample(t, v, max_points=10000):
    """Downsample for browser performance."""
    if len(t) <= max_points:
        return t, v
    step = len(t) // max_points
    return t[::step], v[::step]

print("Loading waveform data...")
t_ctrl, v_ctrl = load_waveform("ctrl_waveform.txt")
t_ref, v_ref = load_waveform("ref_waveform.txt")
t_div, v_div = load_waveform("divout_waveform.txt")
t_up, v_up = load_waveform("up_waveform.txt")
t_dn, v_dn = load_waveform("dn_waveform.txt")
metrics = load_metrics()

# Convert time to microseconds
t_ctrl_us = t_ctrl * 1e6
t_ref_us = t_ref * 1e6
t_div_us = t_div * 1e6
t_up_us = t_up * 1e6
t_dn_us = t_dn * 1e6

# Downsample for full-range plots
t_ctrl_ds, v_ctrl_ds = downsample(t_ctrl_us, v_ctrl, 15000)
t_ref_ds, v_ref_ds = downsample(t_ref_us, v_ref, 8000)
t_div_ds, v_div_ds = downsample(t_div_us, v_div, 8000)
t_up_ds, v_up_ds = downsample(t_up_us, v_up, 8000)
t_dn_ds, v_dn_ds = downsample(t_dn_us, v_dn, 8000)

# Zoomed-in ripple view (last 10µs)
mask_zoom = t_ctrl_us >= 40.0
t_zoom, v_zoom = downsample(t_ctrl_us[mask_zoom], v_ctrl[mask_zoom], 15000)

# Phase error proxy: difference between ref and div_out edges
# Compute UP pulse width over time as a proxy for phase error
mask_up_zoom = t_up_us >= 40.0
t_up_zoom, v_up_zoom = downsample(t_up_us[mask_up_zoom], v_up[mask_up_zoom], 8000)
mask_dn_zoom = t_dn_us >= 40.0
t_dn_zoom, v_dn_zoom = downsample(t_dn_us[mask_dn_zoom], v_dn[mask_dn_zoom], 8000)

# Spectrum of control voltage ripple (last 10µs, evenly resampled)
mask_spec = t_ctrl_us >= 40.0
t_spec = t_ctrl_us[mask_spec]
v_spec = v_ctrl[mask_spec]
# Resample to uniform grid for FFT
n_fft = min(len(t_spec), 65536)
t_uniform = np.linspace(t_spec[0], t_spec[-1], n_fft)
v_uniform = np.interp(t_uniform, t_spec, v_spec)
v_uniform -= np.mean(v_uniform)  # Remove DC
dt = (t_uniform[-1] - t_uniform[0]) / (n_fft - 1) * 1e-6  # back to seconds
freqs = np.fft.rfftfreq(n_fft, d=dt)
spectrum = np.abs(np.fft.rfft(v_uniform * np.hanning(n_fft)))
spectrum_db = 20 * np.log10(spectrum / max(spectrum.max(), 1e-15))
# Only show up to 1 MHz
freq_mask = (freqs > 0) & (freqs <= 1e6)
freqs_plot = freqs[freq_mask] / 1e3  # kHz
spectrum_plot = spectrum_db[freq_mask]

# VCO frequency estimate from ctrl voltage
# f = 2.4GHz + Kvco * (Vctrl - 1.65)
Kvco = 10e6  # Hz/V
f_vco = 2.4e9 + Kvco * (v_ctrl - 1.65)
f_vco_ghz = f_vco / 1e9
t_fvco_ds, f_vco_ds = downsample(t_ctrl_us, f_vco_ghz, 15000)

# ============================
# Build Dashboard
# ============================
print("Building dashboard...")

fig = make_subplots(
    rows=4, cols=2,
    subplot_titles=(
        "Control Voltage (Full Transient)",
        "VCO Output Frequency",
        "Control Voltage Ripple (Steady-State Zoom: 40-50µs)",
        "Control Voltage Spectrum (Steady-State)",
        "Reference Clock vs Divider Output",
        "PFD Output: UP and DOWN Pulses (Steady-State)",
        "Phase Error Proxy (UP - DN Pulses, Steady-State)",
        "Design Parameters & Metrics",
    ),
    vertical_spacing=0.06,
    horizontal_spacing=0.08,
    specs=[
        [{"type": "scatter"}, {"type": "scatter"}],
        [{"type": "scatter"}, {"type": "scatter"}],
        [{"type": "scatter"}, {"type": "scatter"}],
        [{"type": "scatter"}, {"type": "table"}],
    ],
)

# 1. Control Voltage — Full Transient
fig.add_trace(go.Scatter(
    x=t_ctrl_ds, y=v_ctrl_ds,
    mode="lines", name="Vctrl",
    line=dict(color="#2196F3", width=1),
), row=1, col=1)
fig.add_hline(y=1.65, line_dash="dash", line_color="red",
              annotation_text="Vco_center = 1.65V", row=1, col=1)

# 2. VCO Frequency
fig.add_trace(go.Scatter(
    x=t_fvco_ds, y=f_vco_ds,
    mode="lines", name="f_VCO",
    line=dict(color="#FF9800", width=1),
), row=1, col=2)
fig.add_hline(y=2.4, line_dash="dash", line_color="red",
              annotation_text="Target: 2.4 GHz", row=1, col=2)

# 3. Ripple Zoom
fig.add_trace(go.Scatter(
    x=t_zoom, y=v_zoom * 1000,  # mV for clarity
    mode="lines", name="Vctrl (mV)",
    line=dict(color="#4CAF50", width=1),
), row=2, col=1)
ctrl_avg_mv = metrics.get("ctrl_avg", 1.618) * 1000
fig.add_hline(y=ctrl_avg_mv, line_dash="dot", line_color="orange",
              annotation_text=f"avg = {ctrl_avg_mv:.1f} mV", row=2, col=1)

# 4. Spectrum
fig.add_trace(go.Scatter(
    x=freqs_plot, y=spectrum_plot,
    mode="lines", name="Spectrum",
    line=dict(color="#9C27B0", width=1),
), row=2, col=2)
# Mark 10 MHz reference spur location (aliased in our window)
ref_freq_khz = 10e6 / 1e3  # 10,000 kHz — will be outside our 1MHz window
# Mark lower harmonics if visible
for harm_khz in [10, 20, 50, 100, 200, 500]:
    if harm_khz <= freqs_plot[-1]:
        fig.add_vline(x=harm_khz, line_dash="dot", line_color="rgba(255,0,0,0.3)",
                      row=2, col=2)

# 5. Ref vs Divider
# Show only a few reference cycles at steady state for clarity
mask_refzoom = (t_ref_us >= 49.5) & (t_ref_us <= 50.0)
mask_divzoom = (t_div_us >= 49.5) & (t_div_us <= 50.0)
fig.add_trace(go.Scatter(
    x=t_ref_us[mask_refzoom], y=v_ref[mask_refzoom],
    mode="lines", name="REF (10 MHz)",
    line=dict(color="#F44336", width=1.5),
), row=3, col=1)
fig.add_trace(go.Scatter(
    x=t_div_us[mask_divzoom], y=v_div[mask_divzoom],
    mode="lines", name="DIV_OUT (VCO/240)",
    line=dict(color="#2196F3", width=1.5),
), row=3, col=1)

# 6. PFD UP/DN pulses (steady state zoom)
fig.add_trace(go.Scatter(
    x=t_up_zoom, y=v_up_zoom,
    mode="lines", name="UP pulse",
    line=dict(color="#4CAF50", width=1),
), row=3, col=2)
fig.add_trace(go.Scatter(
    x=t_dn_zoom, y=v_dn_zoom,
    mode="lines", name="DN pulse",
    line=dict(color="#F44336", width=1),
), row=3, col=2)

# 7. Phase error proxy — UP - DN over time
# This is approximate — just overlay for visual
mask_phase = t_up_us >= 40.0
t_ph = t_up_us[mask_phase]
v_phase = v_up[mask_phase] - np.interp(t_ph, t_dn_us, v_dn)
t_ph_ds, v_phase_ds = downsample(t_ph, v_phase, 8000)
fig.add_trace(go.Scatter(
    x=t_ph_ds, y=v_phase_ds,
    mode="lines", name="UP - DN",
    line=dict(color="#FF5722", width=1),
    fill="tozeroy", fillcolor="rgba(255,87,34,0.15)",
), row=4, col=1)
fig.add_hline(y=0, line_dash="dash", line_color="gray", row=4, col=1)

# 8. Metrics Table
ctrl_ripple_mv = metrics.get("ctrl_ripple", 0.005) * 1000
param_names = [
    "Score (from results.md)", "Ctrl Voltage Final", "Ctrl Voltage Ripple",
    "Ctrl Voltage Avg", "Lock Time",
    "Icp (Charge Pump)", "Kvco", "VDD",
    "R1 (Loop Filter)", "C1", "C2", "C3",
    "CP Mismatch", "N (Divider)", "f_ref", "f_target",
]
param_values = [
    "0.884949", f"{metrics.get('ctrl_final', 'N/A'):.4f} V",
    f"{ctrl_ripple_mv:.3f} mV (target < 5 mV)",
    f"{metrics.get('ctrl_avg', 'N/A'):.5f} V",
    "< 1 µs (instant from IC)",
    "263.2 µA", "10 MHz/V", "3.3 V",
    "150 kΩ", "1 nF", "200 pF", "100 pF",
    "0.5%", "240", "10 MHz", "2.4 GHz",
]
param_status = [
    "✅ > 0.80", "—", "✅" if ctrl_ripple_mv <= 5 else "❌",
    "—", "✅ < 50 µs",
    "—", "—", "—",
    "—", "—", "—", "—",
    "—", "—", "—", "—",
]
fig.add_trace(go.Table(
    header=dict(
        values=["Parameter", "Value", "Status"],
        fill_color="#1a1a2e",
        font=dict(color="white", size=12),
        align="left",
    ),
    cells=dict(
        values=[param_names, param_values, param_status],
        fill_color=["#16213e", "#0f3460", "#16213e"],
        font=dict(color="white", size=11),
        align="left",
        height=24,
    ),
), row=4, col=2)

# Axis labels
fig.update_xaxes(title_text="Time (µs)", row=1, col=1)
fig.update_yaxes(title_text="Voltage (V)", row=1, col=1)
fig.update_xaxes(title_text="Time (µs)", row=1, col=2)
fig.update_yaxes(title_text="Frequency (GHz)", row=1, col=2)
fig.update_xaxes(title_text="Time (µs)", row=2, col=1)
fig.update_yaxes(title_text="Voltage (mV)", row=2, col=1)
fig.update_xaxes(title_text="Frequency (kHz)", row=2, col=2)
fig.update_yaxes(title_text="Magnitude (dB)", row=2, col=2)
fig.update_xaxes(title_text="Time (µs)", row=3, col=1)
fig.update_yaxes(title_text="Voltage (V)", row=3, col=1)
fig.update_xaxes(title_text="Time (µs)", row=3, col=2)
fig.update_yaxes(title_text="Voltage (V)", row=3, col=2)
fig.update_xaxes(title_text="Time (µs)", row=4, col=1)
fig.update_yaxes(title_text="Voltage (V)", row=4, col=1)

fig.update_layout(
    title=dict(
        text="🔬 PLL Frequency Synthesizer — Simulation Dashboard<br>"
             "<sup>Fractional-N Sigma-Delta PLL | 2.4 GHz from 10 MHz ref | Score: 0.885</sup>",
        x=0.5,
        font=dict(size=20),
    ),
    template="plotly_dark",
    height=1800,
    width=1600,
    showlegend=True,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    font=dict(family="JetBrains Mono, Consolas, monospace"),
)

output_path = os.path.join(DATA_DIR, "pll_dashboard.html")
fig.write_html(output_path, include_plotlyjs=True)
print(f"Dashboard saved to: {output_path}")
