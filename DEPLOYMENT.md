# Hospet Cost Studio — GitHub + Streamlit deployment

## Repository entrypoint

Run the application from the repository root:

```bash
streamlit run app.py
```

For Streamlit Community Cloud use:

- Branch: `main`
- Main file path: `app.py`
- Dependency file: root `requirements.txt`

## Important package structure

`Sinter` and `MBF` are separate Python packages. Their generic filenames are intentionally kept inside their own packages:

```text
sinter/optimizer.py
mbf/optimiser.py
mbf/analytics.py
```

Application code imports them explicitly, for example:

```python
from sinter import optimizer as sopt
from mbf import optimiser as mopt
from mbf import analytics as an
```

The combined application no longer modifies `sys.path` to make same-named modules visible. This prevents import collisions when the project is run from GitHub/Streamlit.

## Data files

The combined application expects the sample input files under `sample_inputs/`. Do not flatten the repository when uploading to GitHub.
