"""Route registration for the modular monolith.

Endpoint functions are being moved progressively. Empty blueprints establish the
stable registration points without changing the existing public routes.
"""

from routes.accounts_routes import bp as accounts_bp
from routes.commands_routes import bp as commands_bp
from routes.data_quality_routes import bp as data_quality_bp
from routes.portfolio_routes import bp as portfolio_bp
from routes.systems_routes import bp as systems_bp


def register_blueprints(app) -> None:
    app.register_blueprint(portfolio_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(systems_bp)
    app.register_blueprint(commands_bp)
    app.register_blueprint(data_quality_bp)

