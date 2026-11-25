# Deploys the demo to Azure App Service. A new deployment uses the FREE (F1) tier and nothing in this script creates a paid resource:
#   - one resource group        (free)
#   - one Linux App Service plan, SKU F1 "Free"   (no charge; 60 CPU-minutes a day, 1 GB memory, 1 GB storage)
#   - one web app on that plan  (free)
# No database, storage account, container registry, Log Analytics, Fabric or Power BI capacity is created.
# A new plan that does not come out on the Free tier stops the script. A plan you have scaled up yourself keeps its tier.
#
# Before running:  install the Azure CLI, then sign in with   az login
# Usage:           .\deploy\azure\deploy.ps1 -AppName my-unique-name [-Location westeurope]
param(
    [Parameter(Mandatory = $true)][string]$AppName,          # becomes https://<AppName>.azurewebsites.net ; must be globally unique
    [string]$Location = "westeurope",
    [string]$ResourceGroup = "rg-hotelchain-demo",
    [string]$Plan = "plan-hotelchain-free"
)
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

if (-not (Get-Command az -ErrorAction SilentlyContinue)) { throw "The Azure CLI is not installed. Install it, open a new terminal, run 'az login', then run this script again." }
$account = az account show --query "{name:name, id:id}" -o json 2>$null | ConvertFrom-Json
if (-not $account) { throw "You are not signed in to Azure. Run 'az login' first." }
Write-Host "Subscription: $($account.name)"

Write-Host "1/5  Building the deployment package"
& .\.venv\Scripts\python.exe deploy\azure\build_package.py
if ($LASTEXITCODE -ne 0) { throw "Package build failed" }

Write-Host "2/5  Resource group $ResourceGroup in $Location"
az group create --name $ResourceGroup --location $Location --output none

Write-Host "3/5  App Service plan"
# A new plan is always created on the Free tier. An existing plan keeps whatever tier it is on: running
# "plan create" again would silently move a plan that was scaled up back to Free.
$planExists = az appservice plan list --resource-group $ResourceGroup --query "[?name=='$Plan'] | length(@)" -o tsv
if ($planExists -ne "1") {
    az appservice plan create --name $Plan --resource-group $ResourceGroup --sku F1 --is-linux --output none
    $sku = az appservice plan show --name $Plan --resource-group $ResourceGroup --query "sku.tier" -o tsv
    if ($sku -ne "Free") { throw "The new plan is on tier '$sku', not Free. Stopping so that nothing chargeable is used. Delete the resource group to clean up." }
} else {
    $sku = az appservice plan show --name $Plan --resource-group $ResourceGroup --query "sku.name" -o tsv
    if ($sku -eq "F1") { Write-Host "     Existing plan, Free tier" }
    else { Write-Warning "The existing plan is on the paid tier $sku and is billed by the hour. The tier is left unchanged." }
}

Write-Host "4/5  Web app $AppName"
# The Free tier allows only about 15 app restarts an hour, and every settings change restarts the app,
# so the app is configured in as few calls as possible and only when it does not exist yet.
$exists = az webapp list --resource-group $ResourceGroup --query "[?name=='$AppName'] | length(@)" -o tsv
if ($exists -ne "1") {
    az webapp create --name $AppName --resource-group $ResourceGroup --plan $Plan --runtime "PYTHON:3.12" --https-only true `
        --startup-file "python -m uvicorn hip.api.app:app --host 0.0.0.0 --port 8000" --output none
    if ($LASTEXITCODE -ne 0) { throw "The web app could not be created. The name may already be taken; try another -AppName." }
}
# The Python libraries are shipped pre-built inside the package (python_packages), so Azure does not build anything.
az webapp config appsettings set --name $AppName --resource-group $ResourceGroup --output none --settings `
    SCM_DO_BUILD_DURING_DEPLOYMENT=false PYTHONPATH=/home/site/wwwroot/python_packages WEBSITES_PORT=8000 `
    HIP_PUBLIC_DEMO=1 HIP_DUCKDB_MEMORY=350MB HIP_DUCKDB_THREADS=1

Write-Host "5/5  Uploading the package (about 135 MB; allow several minutes)"
az webapp deploy --name $AppName --resource-group $ResourceGroup --src-path dist\azure\hotelchain-demo.zip --type zip --timeout 1800 --output none
if ($LASTEXITCODE -ne 0) { throw "The upload did not complete. If the app shows 'QuotaExceeded', the Free tier's hourly limit was reached: wait for the next hour and run this script again." }

$url = "https://$AppName.azurewebsites.net"
Write-Host ""
Write-Host "Deployed: $url"
Write-Host "The Free tier sleeps when idle, so the first page after a pause takes up to a minute to load."
Write-Host "To remove everything:  .\deploy\azure\teardown.ps1"
