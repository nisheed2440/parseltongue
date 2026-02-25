import typer
from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(
    name="parseltongue",
    help="Fan fiction pipeline: scrape → enunciate → speak → consume",
    no_args_is_help=True,
)

from parseltongue_cli.commands import scrape  # noqa: E402

app.add_typer(scrape.app, name="scrape")

if __name__ == "__main__":
    app()
