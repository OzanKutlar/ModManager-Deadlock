"""Console helpers shared by every module (same style as commit.py)."""

from rich.console import Console
from rich.rule import Rule

console = Console()


def ok(message: str) -> None:
    console.print(f'[green]✔[/green] {message}')


def info(message: str) -> None:
    console.print(f'[cyan]ℹ[/cyan] {message}')


def warn(message: str) -> None:
    console.print(f'[yellow]⚠[/yellow] {message}')


def fail(message: str) -> None:
    console.print(f'[bold red]✘[/bold red] {message}')


def dim(message: str) -> None:
    console.print(f'[dim]{message}[/dim]')


def rule(title: str = '') -> None:
    console.print(Rule(f'[bold blue]{title}[/bold blue]' if title else ''))
