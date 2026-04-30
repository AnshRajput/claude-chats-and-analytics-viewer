$pkg = "claude-chats-and-analytics-viewer"

Write-Host ""
Write-Host "  Claude Chats & Analytics Viewer"
Write-Host "  ================================"
Write-Host ""

# 1. Try pipx (already installed)
if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Host "  [1/2] Installing via pipx..."
    pipx install $pkg 2>$null
    if ($LASTEXITCODE -ne 0) { pipx upgrade $pkg }
    Write-Host "  [2/2] Done! Starting viewer..."
    Write-Host ""
    ccv
    exit
}

# 2. Try uv (already installed)
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "  [1/1] Launching via uv..."
    Write-Host ""
    uv tool run $pkg
    exit
}

# 3. Install pipx via pip, then install package
if (Get-Command pip -ErrorAction SilentlyContinue) {
    Write-Host "  [1/3] Installing pipx..."
    pip install --quiet pipx
    pipx ensurepath
    Write-Host "  [2/3] Installing $pkg..."
    pipx install $pkg
    Write-Host "  [3/3] Done!"
    Write-Host ""
    Write-Host "  Note: open a new terminal so 'ccv' is on your PATH, then run: ccv"
    exit
}

Write-Host "  [ERROR] Python not found. Install from https://python.org (check 'Add to PATH')"
exit 1
