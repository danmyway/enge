Name: enge
Version: 0.1.3
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

%files -f %{pyproject_files}
%{_bindir}/enge

%changelog
