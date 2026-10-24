from cookbook_app import create_default_server


def test_default_application_explicitly_composes_recipes(tmp_path):
    server = create_default_server(tmp_path)

    assert server.catalog.recipe_ids == ("oracle", "owner_verification")
    assert server.registered_tool_names == {
        "cookbook_list_recipes",
        "oracle_start",
        "oracle_inspect",
        "oracle_respond",
        "oracle_resume",
        "oracle_launch",
        "owner_verification_start",
        "owner_verification_inspect",
        "owner_verification_resume",
    }
