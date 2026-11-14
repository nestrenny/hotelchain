# Removes the whole Azure demo: the resource group and everything in it (plan and web app).
param([string]$ResourceGroup = "rg-hotelchain-demo")
$ErrorActionPreference = "Stop"
az group show --name $ResourceGroup --query "name" -o tsv | Out-Null
Write-Host "Resources in ${ResourceGroup}:"
az resource list --resource-group $ResourceGroup --query "[].{name:name, type:type}" -o table
$answer = Read-Host "Type the resource group name to delete it and everything above"
if ($answer -ne $ResourceGroup) { Write-Host "Nothing deleted."; exit 0 }
az group delete --name $ResourceGroup --yes --no-wait
Write-Host "Deletion started. It finishes in a few minutes."
