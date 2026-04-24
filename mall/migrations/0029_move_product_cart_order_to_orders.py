from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0028_switch_user_fk_to_bot'),
        ('orders', '0003_move_product_cart_order_from_mall'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name='CartItem'),
                migrations.DeleteModel(name='Order'),
                migrations.DeleteModel(name='Product'),
            ],
        ),
    ]
