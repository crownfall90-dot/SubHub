setTimeout(() => {
  const afParameters = {
    mediaSource: {
      keys: ['utm_source'],
      defaultValue: 'site',
    },
    campaign: {
      keys: ['utm_campaign'],
      defaultValue: 'default',
    },
  };

  if (window.BUILD_BROWSER) {
    afParameters.campaign.defaultValue = `welcome_${window.BUILD_BROWSER}_page`;
  }

  const result = window.AF_SMART_SCRIPT.generateDirectClickURL({
    afParameters: afParameters,
    platform: 'nativepc',
    app_id: 'com.veepn.windows',
    redirectURL: 'https://download.veepn.com/windows/VeePNInstaller.exe',
  });

  if (result) {
    document.querySelectorAll('.js-windows-download-btn').forEach((el) => {
      el.setAttribute('href', result.clickURL);
    });

    const impressionUrl = result.clickURL.replace('/click/', '/impression/');

    fetch(impressionUrl, {
      method: 'GET',
      headers: {
        accept: 'application/json',
        'content-type': 'application/json',
      },
      mode: 'no-cors',
    }).catch(() => {});
  }
}, 1000);
