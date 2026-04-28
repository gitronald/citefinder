"""citefinder CLI."""

import typer

app = typer.Typer(help="citefinder")


@app.command()
def hello() -> None:
    """Say hello."""
    print("Hello from citefinder!")
