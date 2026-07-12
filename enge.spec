Name: enge
Version: 2026.7.12
Release: 1%{?dist}

Summary: enge
License: MIT
BuildArch: noarch

URL: https://github.com/danmyway/enge
Source0: https://github.com/danmyway/enge/releases/download/%{version}/enge-%{version}.tar.gz

%generate_buildrequires
%pyproject_buildrequires

%description
enge

%prep
%autosetup


%build
%pyproject_wheel


%install
%pyproject_install
%pyproject_save_files enge
# Install config directory and example/default configs
install -d %{buildroot}%{_sysconfdir}/enge
# Bundled example default (guidance only)
install -m 0644 src/enge/utils/enge_default_config.toml %{buildroot}%{_sysconfdir}/enge/enge_default_config.toml
# Create an empty user config if not present (left for admin to fill in)
# Ship as noreplace so upgrades do not clobber local changes
if [ ! -f %{buildroot}%{_sysconfdir}/enge/enge_user_config.toml ]; then
  touch %{buildroot}%{_sysconfdir}/enge/enge_user_config.toml
fi
chmod 0644 %{buildroot}%{_sysconfdir}/enge/enge_user_config.toml

%files -f %{pyproject_files}
%{_bindir}/enge
%config(noreplace) %{_sysconfdir}/enge/enge_user_config.toml
%config(noreplace) %{_sysconfdir}/enge/enge_default_config.toml

%changelog
