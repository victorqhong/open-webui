import { AZURE_API_BASE_URL } from '$lib/constants';

export const verifyAzureConnection = async (
    token: string = '',
    url: string = '',
    key: string = ''
) => {
    let error = null;

    const res = await fetch(`${AZURE_API_BASE_URL}/verify`, {
        method: 'POST',
        headers: {
            Accept: 'application/json',
            Authorization: `Bearer ${token}`,
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            url,
            key
        })
    })
        .then(async (res) => {
            if (!res.ok) throw await res.json();
            return res.json();
        })
        .catch((err) => {
            error = `Azure: ${err?.error?.message ?? 'Network Problem'}`;
            return [];
        });

    if (error) {
        throw error;
    }

    return res;
};

export const getAzureConfig = async (token: string = '') => {
    let error = null;

    const res = await fetch(`${AZURE_API_BASE_URL}/config`, {
        method: 'GET',
        headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(token && { authorization: `Bearer ${token}` })
        }
    })
        .then(async (res) => {
            if (!res.ok) throw await res.json();
            return res.json();
        })
        .catch((err) => {
            console.log(err);
            if ('detail' in err) {
                error = err.detail;
            } else {
                error = 'Server connection failed';
            }
            return null;
        });

    if (error) {
        throw error;
    }

    return res;
};

type AzureConfig = {
    ENABLE_Azure_API: boolean;
    AZURE_API_BASE_URLS: string[];
    AZURE_API_CONFIGS: object;
};

export const updateAzureConfig = async (token: string = '', config: AzureConfig) => {
    let error = null;

    const res = await fetch(`${AZURE_API_BASE_URL}/config/update`, {
        method: 'POST',
        headers: {
            Accept: 'application/json',
            'Content-Type': 'application/json',
            ...(token && { authorization: `Bearer ${token}` })
        },
        body: JSON.stringify({
            ...config
        })
    })
        .then(async (res) => {
            if (!res.ok) throw await res.json();
            return res.json();
        })
        .catch((err) => {
            console.log(err);
            if ('detail' in err) {
                error = err.detail;
            } else {
                error = 'Server connection failed';
            }
            return null;
        });

    if (error) {
        throw error;
    }

    return res;
};

export const getAzureModels = async (token: string, urlIdx?: number) => {
    let error = null;

    const res = await fetch(
        `${AZURE_API_BASE_URL}/models${typeof urlIdx === 'number' ? `/${urlIdx}` : ''}`,
        {
            method: 'GET',
            headers: {
                Accept: 'application/json',
                'Content-Type': 'application/json',
                ...(token && { authorization: `Bearer ${token}` })
            }
        }
    )
        .then(async (res) => {
            if (!res.ok) throw await res.json();
            return res.json();
        })
        .catch((err) => {
            error = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;
            return [];
        });

    if (error) {
        throw error;
    }

    return res;
};
