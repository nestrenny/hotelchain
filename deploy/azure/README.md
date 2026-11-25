# Public demo on Azure at no cost

This hosts the prototype as a public website on **Azure App Service, Free (F1) tier**. It does not build the target
architecture in the SRS (Data Factory, Fabric, Power BI): those have no free tier. The pipeline and models run on
your machine; Azure only serves the finished results.

## What is created, and what it costs

| Resource | Tier | Cost |
|---|---|---|
| Resource group `rg-hotelchain-demo` | n/a | Free |
| App Service plan `plan-hotelchain-free` (Linux) | **F1 Free** | Free |
| Web app `<your name>.azurewebsites.net` | on the Free plan | Free |

Nothing else is created: no database, storage account, container registry, Log Analytics, Fabric or Power BI.
`deploy.ps1` creates a new plan on the Free tier and stops if it does not come out Free. If you scale an existing plan up yourself (for example to Basic B1, which removes the limits below and allows Always On), later deployments keep that tier and print a warning that it is billed by the hour. To return to Free, turn Always On off and scale the plan back to F1.

An Azure subscription is still required, and Microsoft asks for a payment card to open one, even though these
resources are not charged. A subscription allows one Free Linux plan per region.

## Limits of the Free tier

- **60 CPU-minutes a day.** Each page uses a fraction of a second, so this covers thousands of page views. If it is used up, the site returns an error until the next day (UTC).
- **About 165 MB of data sent a day.** A first visit sends roughly 0.5 MB (the hero image is shipped as a compact JPEG and page data is compressed), so this covers a few hundred visits a day.
- **About 15 app restarts an hour and 3 CPU-minutes in any 5 minutes.** Azure cannot install the Python libraries on this tier without hitting these limits, so the package carries them pre-built. If a limit is hit the app shows `QuotaExceeded` and returns 403 until the hour or day rolls over.
- **1 GB memory, 1 GB storage.** The package unpacks to about 320 MB. Queries are capped at 350 MB.
- **Sleeps when idle.** The first page after a pause takes up to a minute. Open the site a few minutes before a demo.
- **No custom domain or uptime guarantee.** The address is `https://<name>.azurewebsites.net`.

## Steps

```powershell
winget install --exact --id Microsoft.AzureCLI      # once; then open a new terminal
az login                                            # sign in with the account that owns the subscription
.\deploy\azure\deploy.ps1 -AppName <globally-unique-name> -Location westeurope
```

The script builds `dist/azure/hotelchain-demo.zip` (about 135 MB), creates the three resources, and uploads the package.
Nothing is built on Azure; allow a few minutes for the upload. Without admin rights the CLI can instead be installed with
`pip install azure-cli` into its own virtual environment.

To publish new data or code, run the pipeline locally, then run `deploy.ps1` again with the same name.
To remove everything: `.\deploy\azure\teardown.ps1`.

## What a public visitor can do

- **See everything.** Sign-in is simulated: anyone with the link can choose any role, including the CEO. All data is synthetic, and responses carry a header asking search engines not to index the site.
- **Record decisions.** Visitors can approve or reject recommendations and act on alerts. These are stored on the web app's own disk (`data/app.db`) and stay until that file or the app is deleted.
- **Not** change data, run the pipeline or reach your machine.

## What is in the package

`build_package.py` copies the code, web client, configuration and hero image, and writes a slim lakehouse holding the
Gold, AI and operations tables the pages read (no Bronze; three Silver tables). `python_packages/` holds five
run-time libraries and their dependencies (FastAPI, Uvicorn, DuckDB, pandas, NumPy) as Linux builds for Python 3.12;
the machine-learning and test libraries are left out because the web app never trains a model.
