from alembic import context

from app.models import Base


def run_migrations_online() -> None:
    # Quem chama pode passar a própria conexão (os testes de migration usam um banco à parte).
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    from app.db import engine

    with engine.connect() as connection:
        _run(connection)


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
