{
  description = "Simple Python Project";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3Packages.python.withPackages (ps: [
          ps.requests
          ps.sqlalchemy
          ps.flask
          ps.waitress
        ]);

        feeds_cli =
          let
            lines = pkgs.lib.splitString "\n" (builtins.readFile ./main.py);
            feedsCliUsePyEnv = builtins.concatStringsSep "\n" ([ "#!${python}/bin/python" ] ++ builtins.tail lines);
          in
            pkgs.writeTextFile {
              name = "feeds-cli-bin";
              text = feedsCliUsePyEnv;
              executable = true;
              destination = "/bin/feeds-cli";
            };
      in {
        packages.default = pkgs.symlinkJoin {
          name = "example-python-project";
          paths = [
            python
            feeds_cli
          ];
        };

        apps = {
          default = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/feeds-cli";
          };
        };
      }
    );
}
