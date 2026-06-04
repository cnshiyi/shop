# Generated manually to add MySQL comments for Django system tables.

from django.db import migrations


TABLE_COMMENTS = {
    'auth_group': 'Django 权限组表',
    'auth_group_permissions': 'Django 权限组与权限关联表',
    'auth_permission': 'Django 权限定义表',
    'auth_user': 'Django 后台用户表',
    'auth_user_groups': 'Django 用户与权限组关联表',
    'auth_user_user_permissions': 'Django 用户与权限关联表',
    'django_content_type': 'Django 内容类型表',
    'django_migrations': 'Django 数据库迁移记录表',
    'django_session': 'Django 会话表',
}

COLUMN_COMMENTS = {
    'auth_group': {
        'id': '主键ID',
        'name': '权限组名称',
    },
    'auth_group_permissions': {
        'id': '主键ID',
        'group_id': '权限组ID',
        'permission_id': '权限ID',
    },
    'auth_permission': {
        'id': '主键ID',
        'name': '权限名称',
        'content_type_id': '内容类型ID',
        'codename': '权限代码',
    },
    'auth_user': {
        'id': '主键ID',
        'password': '密码哈希',
        'last_login': '最近登录时间',
        'is_superuser': '是否超级管理员',
        'username': '用户名',
        'first_name': '名',
        'last_name': '姓',
        'email': '邮箱',
        'is_staff': '是否后台员工',
        'is_active': '是否启用',
        'date_joined': '加入时间',
    },
    'auth_user_groups': {
        'id': '主键ID',
        'user_id': '用户ID',
        'group_id': '权限组ID',
    },
    'auth_user_user_permissions': {
        'id': '主键ID',
        'user_id': '用户ID',
        'permission_id': '权限ID',
    },
    'django_content_type': {
        'id': '主键ID',
        'app_label': '应用标签',
        'model': '模型名称',
    },
    'django_migrations': {
        'id': '主键ID',
        'app': '应用标签',
        'name': '迁移名称',
        'applied': '应用时间',
    },
    'django_session': {
        'session_key': '会话键',
        'session_data': '会话数据',
        'expire_date': '过期时间',
    },
}


def _sql_literal(value):
    return "'" + str(value).replace('\\', '\\\\').replace("'", "''") + "'"


def _default_sql(column_default, data_type):
    if column_default is None:
        return ''
    default_text = str(column_default)
    upper_default = default_text.upper()
    if upper_default == 'NULL':
        return ' DEFAULT NULL'
    if upper_default in {'CURRENT_TIMESTAMP', 'CURRENT_TIMESTAMP()'}:
        return f' DEFAULT {default_text}'
    if data_type in {
        'bigint',
        'bit',
        'decimal',
        'double',
        'float',
        'int',
        'integer',
        'mediumint',
        'smallint',
        'tinyint',
    }:
        return f' DEFAULT {default_text}'
    return f' DEFAULT {_sql_literal(default_text)}'


def _comment_columns(apps, schema_editor, comments):
    connection = schema_editor.connection
    cursor = connection.cursor()
    quote_name = schema_editor.quote_name
    for table_name, column_comments in comments.items():
        cursor.execute(
            """
            SELECT column_name, column_type, data_type, is_nullable, column_default, extra
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY ordinal_position
            """,
            [table_name],
        )
        columns = {row[0]: row for row in cursor.fetchall()}
        for column_name, comment in column_comments.items():
            column = columns.get(column_name)
            if not column:
                continue
            _, column_type, data_type, is_nullable, column_default, extra = column
            null_sql = ' NULL' if is_nullable == 'YES' else ' NOT NULL'
            default_sql = _default_sql(column_default, data_type)
            extra_sql = f' {extra}' if extra else ''
            schema_editor.execute(
                f'ALTER TABLE {quote_name(table_name)} '
                f'MODIFY COLUMN {quote_name(column_name)} {column_type}'
                f'{null_sql}{default_sql}{extra_sql} COMMENT {_sql_literal(comment)}'
            )


def add_mysql_comments(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    quote_name = schema_editor.quote_name
    for table_name, comment in TABLE_COMMENTS.items():
        schema_editor.execute(
            f'ALTER TABLE {quote_name(table_name)} COMMENT = {_sql_literal(comment)}'
        )
    _comment_columns(apps, schema_editor, COLUMN_COMMENTS)


def clear_mysql_comments(apps, schema_editor):
    if schema_editor.connection.vendor != 'mysql':
        return
    quote_name = schema_editor.quote_name
    for table_name in TABLE_COMMENTS:
        schema_editor.execute(f'ALTER TABLE {quote_name(table_name)} COMMENT = {_sql_literal("")}')
    empty_comments = {
        table_name: {column_name: '' for column_name in column_comments}
        for table_name, column_comments in COLUMN_COMMENTS.items()
    }
    _comment_columns(apps, schema_editor, empty_comments)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0014_alter_cloudaccountconfig_id_alter_externalsynclog_id_and_more'),
        ('sessions', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(add_mysql_comments, clear_mysql_comments),
    ]
