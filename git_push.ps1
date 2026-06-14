#!/usr/bin/env pwsh
param(
    [string]$GitHubUser,
    [string]$RepoName = "Clean-CDrive"
)
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

# Check Git
$gitBin = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitBin) {
    Write-Host "Installing Git via winget..." -ForegroundColor Cyan
    winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Git install failed. Manual: https://git-scm.com" }
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ";$env:Path"
}

# Configure Git if needed
git config --global user.name 2>$null
if (-not $?) {
    $un = Read-Host "Enter your GitHub username"
    $ue = Read-Host "Enter your GitHub email"
    git config --global user.name $un
    git config --global user.email $ue
}

# Get GitHub username
if (-not $GitHubUser) {
    $GitHubUser = Read-Host "Enter your GitHub username"
}

# Init repo
Set-Location $ROOT
if (-not (Test-Path ".git")) {
    git init
    git add -A
    git commit -m "Initial commit: Clean-CDrive"
}

# Setup remote
$remote = "https://github.com/$GitHubUser/$RepoName.git"
$existing = git remote get-url origin 2>$null
if (-not $?) {
    git remote add origin $remote
} else {
    git remote set-url origin $remote
}

# Instructions
Write-Host ""
Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  Next: create the repo on GitHub" -ForegroundColor Yellow
Write-Host "  1. Open https://github.com/new" -ForegroundColor White
Write-Host "  2. Repo name: $RepoName" -ForegroundColor White
Write-Host "  3. Set Public or Private" -ForegroundColor White
Write-Host "  4. Click Create repository" -ForegroundColor White
Write-Host "  5. Press Enter here to push" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow
Read-Host "Press Enter"

# Push
git branch -M main
git push -u origin main

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Done!" -ForegroundColor Green
Write-Host "  https://github.com/$GitHubUser/$RepoName" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Green
