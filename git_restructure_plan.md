# Git Repository Restructure & Version Update Plan

## Phase 1: Repository Restructure

### Current Problem
- Git repository nested in TRADE/ subdirectory
- .git location: TRADE/.git

### Implementation Steps

1. **Move .git directory**
```bash
mv TRADE/.git . && rm -rf TRADE/.git
```

2. **Update Git configuration**
```bash
git config --local --replace-all core.worktree "../"
git reset --hard
```

3. **Verify file tracking**
```bash
git status --porcelain
git rev-parse --show-toplevel
```

## Phase 2: Version Update (0.1.0 → 0.2.0)

### Files to Modify

1. `setup.py` (Line 5):
```diff
-     version="0.1.0",
+     version="0.2.0",
```

2. `TRADE/__init__.py` (Line 17):
```diff
- __version__ = '0.1.0'
+ __version__ = '0.2.0'
```

## Phase 3: GitHub Deployment

```bash
git add .
git commit -m "Restructure repo root and update to v0.2.0"
git push -f origin main
```

## Safety Measures
1. Create backup branch:
```bash
git branch backup-pre-restructure
```

2. Verify installation:
```bash
pip install .
python -c "import TRADE; print(TRADE.__version__)"