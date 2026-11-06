import os
import subprocess


def dump_postgres_db(
    db_name, username, output_file, password=None, host="localhost", port=5432
):
    dump_command = [
        "pg_dump",
        "-h",
        host,
        "-p",
        str(port),
        "-U",
        username,
        "-F",
        "c",  # Custom format
        "-f",
        output_file,
        db_name,
    ]

    command_environment = os.environ.copy()
    if password is not None:
        command_environment["PGPASSWORD"] = password

    try:
        subprocess.run(
            dump_command,
            check=True,
            text=True,
            capture_output=True,
            env=command_environment,
        )
    except FileNotFoundError as error:
        raise RuntimeError(
            "pg_dump is not installed or is not available on the application PATH."
        ) from error
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or "").strip()
        message = f"pg_dump failed with exit code {error.returncode}."
        if details:
            message = f"{message} {details}"
        raise RuntimeError(message) from error
