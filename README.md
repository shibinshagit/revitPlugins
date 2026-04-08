# revitPlugins

pyRevit extension **Uniqube** for Revit (team distribution).

## Layout (matches Google Drive + pyRevit)

pyRevit custom path should be the folder that **directly** contains `Uniqube.extension` (not a parent that only has subfolders without `.extension`).

Example:

```text
G:\My Drive\ENGINEERING_TEAM_TAB\
  Uniqube.extension\   ← this repo’s folder
```

## Use on a new PC (git pull)

**Option A — folder is already your Drive root**

```bash
cd "G:\My Drive\ENGINEERING_TEAM_TAB"
git clone https://github.com/shibinshagit/revitPlugins.git .
```

(Only if that folder is empty except what you want to replace; otherwise clone into a temp dir and copy `Uniqube.extension` over.)

**Option B — clone elsewhere, then copy**

```bash
git clone https://github.com/shibinshagit/revitPlugins.git
xcopy /E /I revitPlugins\Uniqube.extension "G:\My Drive\ENGINEERING_TEAM_TAB\Uniqube.extension"
```

**Updates**

```bash
cd <path-to-repo>
git pull
```

Then **pyRevit → Reload** (or restart Revit).

## Push changes (authoring machine)

```bash
git add -A
git commit -m "Describe change"
git push origin main
```

Use `master` instead of `main` if that is your default branch.
