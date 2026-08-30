import psycopg

DATABASE_URL = "dbname=appointment_bot user=akshaykumar"


def get_connection():
    return psycopg.connect(DATABASE_URL)
